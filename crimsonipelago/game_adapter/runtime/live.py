"""Live item delivery through the in-game plugin (R3/R4 made routine).

While the game runs with the plugin loaded, inventory items are given the way the game gives loot: one
add-check-commit of the game's own per item, called on the game-logic thread between two of its tasks
(docs/research/r2-money.md). The client's delivery engine uses this before falling back to editing the save
once the game is closed.

Nothing here blocks for long: the transaction is found by a watch that stays armed between client ticks
(the game makes an "add" of its own every few seconds), and a queued call is cancelled after a few seconds
if the game doesn't get to it (a menu, a loading screen).
"""

from __future__ import annotations

import json
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .bridge import BridgeError, ResearchBridge
from .research import (
    LIVE_ITEM_NO_BASE,
    RECORD_SIZE,
    TARGETS_FILE,
    ItemTable,
    Probe,
    build_gift,
    load_targets,
    read_item_table,
)

MIN_PLUGIN = (0, 8)
CONNECT_EVERY = 5.0          # seconds between attempts to reach the plugin
PROBE_FRESH = 300.0          # a transaction seen longer ago than this is looked for again. It stays the same
                             # all session (r6-edges); a load changes its owner, which _fresh_probe checks
JOB_WAIT = 6.0               # seconds the game gets to make a queued call


LEARN_FROM = 25              # "how it was learned", as the saves record story-given knowledge such as Flight
TX_STATE = 0x1C0             # bytes of the game's transaction kept with a refusal (what state it was in)
SIGHTINGS = 2000             # transaction sightings kept in memory (the edge-case test reads them)
TRANSIENT_TRIES = 5          # an item the game took before but turns down now: tries before leaving it for the save
# Phase 2 research: what each of the game's own transactions changes. "add" takes &removals as its 4th argument
# and &additions as its 6th, each {pointer to 0x38-byte entries, count}; the plugin saves 16 entries of each.
PROBE_FOLLOW = "5:0:896,3:0:896"
ENTRY = 0x38
BUSY_CODES = {0x40128A9B}    # "not now" from the game after a run of items in a row (live log, 25 Oct 2026)


class JsonlLog:
    """The live delivery log: one JSON line per event in <folder>/live-YYYYMMDD.jsonl. Best effort: a log that
    can't be written never stops a delivery."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)

    def __call__(self, record: dict) -> None:
        now = datetime.now()
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            with open(self.folder / f"live-{now:%Y%m%d}.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"at": now.isoformat(timespec="milliseconds"), **record}) + "\n")
        except OSError:
            pass


def build_knowledge_gift(key: int, level: int, learn_from: int, mem: int) -> tuple[bytes, dict[str, int]]:
    """Our memory block for learning knowledge: one change entry of kind 4 {u32 key at +8, u32 level at +0xC,
    u8 how-learned at +0x10} (as the skill menu writes it: docs/research/r5-knowledge.md), the list
    {entries, 1, 1}, an empty list and the result. Same layout as build_gift, without the item."""
    layout = {"entry": mem + 0x100, "adds": mem + 0x140, "none": mem + 0x160, "result": mem + 0x180}
    block = bytearray(0x200)
    entry = bytearray(0x38)
    entry[0] = 4
    struct.pack_into("<IIB", entry, 0x08, key, level, learn_from)
    block[0x100:0x138] = entry
    struct.pack_into("<QII", block, 0x140, layout["entry"], 1, 1)
    struct.pack_into("<QII", block, 0x160, 0, 0, 0)
    struct.pack_into("<I", block, 0x180, 0xFFFFFFFF)
    return bytes(block), layout


def stackable_record(handle: int, count: int, item_no: int, unix_time: int) -> bytes:
    """An item record for a stackable item, as the game's own stacks look (a Copper stack, read in R3; the game
    accepted the same layout for an ore it had never seen). Pointer-free: everything a copy needs is inline."""
    rec = bytearray(RECORD_SIZE)
    struct.pack_into("<Q", rec, 0x00, item_no)
    struct.pack_into("<H", rec, 0x08, handle)
    struct.pack_into("<Q", rec, 0x10, count)
    struct.pack_into("<II", rec, 0x28, 1, 1)
    struct.pack_into("<Q", rec, 0x38, 0x16AA1)
    struct.pack_into("<H", rec, 0x40, 0xFFFF)
    struct.pack_into("<I", rec, 0x8C, 0xFFFFFFFF)
    struct.pack_into("<H", rec, 0x90, handle)
    struct.pack_into("<Q", rec, 0x98, unix_time)
    return bytes(rec)


@dataclass
class LiveResult:
    state: str                   # "given", "refused", "unknown item", "not now"
    message: str = ""
    code: int | None = None
    detail: dict = field(default_factory=dict)

    @property
    def given(self) -> bool:
        return self.state == "given"


def _entries(listing: str | None, data: str | None) -> list[dict]:
    """Change entries from a {pointer, count} argument snapshot and the entries it points at (as saved)."""
    if not listing or not data:
        return []
    raw = bytes.fromhex(listing)
    if len(raw) < 12:
        return []
    count = struct.unpack_from("<I", raw, 8)[0]
    body = bytes.fromhex(data)
    out = []
    for n in range(min(count, len(body) // ENTRY)):
        e = body[n * ENTRY:(n + 1) * ENTRY]
        out.append({"kind": e[0], "a": struct.unpack_from("<I", e, 8)[0], "b": struct.unpack_from("<I", e, 0xC)[0],
                    "hex": e.hex()})
    if count > len(out):
        out.append({"more": count - len(out)})
    return out


def changes(record: dict) -> dict:
    """What one of the game's own transactions adds and removes (Phase 2 research: do quests go this way too?)."""
    memory = record.get("memory") or {}
    follow = {f.get("spec"): f.get("hex") for f in record.get("follow") or []}
    args = record.get("args") or []
    out = {"adds": _entries(memory.get("5"), follow.get("5:0:896")),
           "removes": _entries(memory.get("3"), follow.get("3:0:896"))}
    if len(args) > 8:
        out["args"] = args[5:9]
    return {k: v for k, v in out.items() if v}


def _version(text: str) -> tuple[int, int]:
    try:
        major, minor = text.split(".")[:2]
        return int(major), int(minor)
    except ValueError:
        return (0, 0)


class LiveChannel:
    """The client's link to the plugin for live delivery. One instance per client run."""

    def __init__(self, port: int | None = None, *, on_event: Callable[[str], None] = lambda m: None,
                 bridge_factory: Callable[..., ResearchBridge] = ResearchBridge, targets_file=TARGETS_FILE,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 log: Callable[[dict], None] | None = None, found_file: Path | None = None):
        self.port, self.on_event, self.bridge_factory = port, on_event, bridge_factory
        self.targets_file, self.clock, self.sleep = targets_file, clock, sleep
        self.log = log or (lambda record: None)
        self.found_file = found_file          # builds found by signature (phase 1b), kept between runs
        self._search_failed: dict[int, str] = {}   # build -> why the signature search didn't find everything
        self.sightings: list[dict] = []       # every use of the game's "add" seen: when, by whom, on what
        self.loads = 0                        # saves loaded while connected (the owner changed or the clock went back)
        self.load_at = 0.0                    # wall-clock time the latest load was noticed
        self.last_seen_unix: int | None = None   # the game's own time of the latest use seen
        self._last_seen: Probe | None = None
        self.bridge: ResearchBridge | None = None
        self.r3: dict | None = None
        self.base = 0
        self.table: ItemTable | None = None
        self.mem: int | None = None
        self.probe: Probe | None = None
        self.probe_seen = 0.0
        self.probing = False
        self.unsupported: str | None = None   # why live delivery can't work with this game/plugin
        self.refused: dict = {}               # item key (or ("knowledge", key)) -> the game's result code
        self.accepted: set[int] = set()       # item keys the game took live this session
        self.turned_down: dict[int, int] = {} # item key -> refusals in a row of an item it took before
        self.seen_count = 0                   # uses of the game's own "add" seen so far
        self._await_seen: int | None = None   # after a refusal: wait until the game has made an add of its own
        self._last_logged: tuple | None = None
        self.trace: list[int] = []            # research: game functions to watch during a call (3 at most)
        self._last_attempt = -1e9

    # ------------------------------------------------------------------ connection
    @property
    def learns_knowledge(self) -> bool:
        """Whether knowledge (Flight) goes in live too: only once verified for this game build (targets.json)."""
        return bool(self.r3 and self.r3.get("knowledge_live"))

    @property
    def connected(self) -> bool:
        return self.bridge is not None and self.bridge.connected

    def ready(self) -> bool:
        """Connected to a plugin that can deliver for this game build (tries to connect now and then)."""
        if self.connected:
            return self.unsupported is None
        if self.clock() - self._last_attempt < CONNECT_EVERY:
            return False
        self._last_attempt = self.clock()
        bridge = self.bridge_factory(port=self.port, timeout=5.0) if self.port else self.bridge_factory(timeout=5.0)
        try:
            bridge.connect()
        except BridgeError:
            return False
        return self.attach(bridge)

    def attach(self, bridge: ResearchBridge) -> bool:
        """Use a bridge that is already connected (the research commands share theirs)."""
        info = bridge.info
        assert info is not None
        self.bridge, self.base = bridge, int(info.module_base, 16)
        self.table, self.mem, self.probe, self.probing, self.refused = None, None, None, False, {}
        self._last_seen, self.last_seen_unix = None, None
        self.unsupported = None
        build = load_targets(info.pe_timestamp, self.targets_file)
        known = build["r3"] if build and "item_table" in build.get("r3", {}) else None
        if _version(info.plugin_version) < MIN_PLUGIN:
            self.unsupported = (f"the game plugin is {info.plugin_version}; live delivery needs "
                                f"{MIN_PLUGIN[0]}.{MIN_PLUGIN[1]}.0 or newer")
        elif known is None and (known := self._find_targets(bridge, info)) is None:
            self.unsupported = (f"live delivery doesn't know this game version yet (build stamp {info.pe_timestamp}: "
                                f"{self._search_failed.get(info.pe_timestamp, 'no signatures')}); items arrive when "
                                "you quit to desktop")
        if known is not None and self.unsupported is None:
            self.r3 = {k: int(v, 16) if isinstance(v, str) and v.startswith("0x") else v for k, v in known.items()}
        self.on_event(f"Game plugin connected ({info.describe()})"
                      + (f": {self.unsupported}" if self.unsupported else "; items arrive while you play"))
        self.log({"event": "connected", "plugin": info.plugin_version, "build": info.pe_timestamp,
                  "game": info.exe_version, "unsupported": self.unsupported})
        return self.unsupported is None

    def _find_targets(self, bridge: ResearchBridge, info) -> dict | None:
        """A game version not in targets.json: look for the functions by signature, once per version (phase 1b)."""
        from .signatures import FoundTargets, load_signatures, r3_from, reader, scan

        stamp = info.pe_timestamp
        record = FoundTargets(self.found_file) if self.found_file else None
        if record is not None and (r3 := record.get(stamp)) is not None:
            return r3
        if stamp in self._search_failed:
            return None
        try:
            sigs, defaults = load_signatures(self.targets_file)
        except (OSError, ValueError, KeyError):
            sigs, defaults = {}, {}
        if not sigs:
            self._search_failed[stamp] = "no signatures"
            return None
        self.on_event(f"Game version {info.exe_version} is new to Crimsonipelago: looking for the game's functions "
                      "(about a minute, once per game version)...")
        try:
            found = scan(reader(bridge), int(info.module_base, 16), sigs)
        except ValueError as exc:
            self._search_failed[stamp] = str(exc)
            return None
        self.log({"event": "signatures", "build": stamp, "found": {k: hex(v) for k, v in found.targets.items()},
                  "problems": found.problems, "seconds": found.seconds})
        if not found.complete:
            self._search_failed[stamp] = "; ".join(f"{k} {v}" for k, v in found.problems.items())
            return None
        r3 = r3_from(found, defaults)
        if record is not None:
            record.put(stamp, r3, info.exe_version, found)
        self.on_event(f"Found them all ({found.seconds:.0f} s)")
        return r3

    def drop(self, why: str = "") -> None:
        if self.bridge is not None:
            try:
                self.bridge.close()
            finally:
                self.bridge = None
                self.on_event("Game plugin disconnected" + (f" ({why})" if why else " (game closed?)"))
                self.log({"event": "disconnected", "why": why})
        self.probe, self.probing = None, False

    # ------------------------------------------------------------------ the game's transaction
    def _update_probe(self) -> None:
        """Keep a watch on the game's own "add" armed and remember the latest transaction it used. Never waits:
        the game makes such calls by itself every so often, and each client tick reads what came in."""
        assert self.bridge is not None and self.r3 is not None
        if not self.probing:
            while len(self.bridge.hits(8).records) == 8:   # leftovers from earlier watches
                pass
            self.bridge.watch(self.base + self.r3["probe"], 1, "execute", follow=PROBE_FOLLOW)
            self.probing = True
        for r in self.bridge.hits(8).records:
            args = [int(a, 16) for a in r.get("args", [])]
            if len(args) >= 11:
                seen = Probe(args[0], args[2], int(r["tid"]), args[9] & 0xFFFFFFFF, args[10] & 0xFFFFFFFF,
                             (r.get("frames") or ["?"])[0])
                self._sighted(seen, changes(r))
                self.probe = seen
                self.probe_seen = self.clock()

    def _sighted(self, seen: Probe, change: dict | None = None) -> None:
        """Remember a use of the game's "add"; log when the transaction, its owner or the thread changed, or the
        game's clock went back. A new owner or the clock going back is a save being loaded: loading a save gave
        the player a new owner and set the clock back each time, and nothing else did (fast travel, the horse,
        dying: research/phase1-edge-cases.md)."""
        sighting = {"t": round(self.clock(), 3), "tx": hex(seen.tx), "owner": hex(seen.owner), "tid": seen.tid,
                    "caller": seen.caller, "clock": seen.clock, "unix": seen.unix_time}
        self.sightings.append(sighting)
        del self.sightings[:-SIGHTINGS]
        self.seen_count += 1
        self.log({"event": "seen", "owner": sighting["owner"], "caller": seen.caller, "clock": seen.clock,
                  **(change or {})})
        last, self._last_seen = self._last_seen, seen
        self.last_seen_unix = seen.unix_time
        if last is None:
            self.log({"event": "transaction", **sighting})
            return
        changed = [name for name, a, b in (("tx", last.tx, seen.tx), ("owner", last.owner, seen.owner),
                                           ("thread", last.tid, seen.tid)) if a != b]
        went_back = 0 < last.clock - seen.clock < 1 << 31            # (a 32-bit clock wrapping is not a load)
        if went_back:
            changed.append("clock went back")
        if changed:
            self.log({"event": "transaction", "changed": changed, "clock_before": last.clock, **sighting})
        if went_back or seen.owner != last.owner:
            self.loads += 1
            self.load_at = time.time()
            self.log({"event": "load", "loads": self.loads, "owner_before": hex(last.owner),
                      "clock_before": last.clock, **sighting})

    def _fresh_probe(self) -> Probe | None:
        """The game-logic thread's transaction, seen within the last PROBE_FRESH seconds; None while waiting."""
        assert self.bridge is not None
        self._update_probe()
        if self._await_seen is not None:
            if self.seen_count <= self._await_seen:
                return None
            self._await_seen = None
        if self.probe is None or self.clock() - self.probe_seen >= PROBE_FRESH:
            return None
        try:
            head = self.bridge.read(self.probe.tx, 8)
        except BridgeError:
            if not self.bridge.connected:
                raise
            head = b"\xff" * 8
        if int.from_bytes(head, "little") not in (self.probe.owner, 0):
            self.probe = None
            return None
        return self.probe

    def poll(self) -> None:
        """Called every client tick: connect when the game starts, and keep the transaction fresh, so an item
        that arrives can go in at once."""
        if not self.ready() or self.bridge is None:
            return
        try:
            self._update_probe()
        except BridgeError as exc:
            self.drop(str(exc))

    # ------------------------------------------------------------------ giving
    def give(self, item_key: int, count: int) -> LiveResult:
        if not self.ready():
            return LiveResult("not now", self.unsupported or "the game plugin isn't connected")
        try:
            result = self._give(item_key, count)
        except BridgeError as exc:
            self.drop(str(exc))
            result = LiveResult("not now", f"lost the game plugin ({exc})")
        self._logged("item", item_key, result, count=count)
        return result

    def _logged(self, what: str, key: int, result: LiveResult, **extra) -> None:
        job = result.detail.get("job") or {}
        record = {"event": "give", "what": what, "key": key, **extra, "state": result.state, "code": result.code,
                  "message": result.message, "waited_ms": job.get("waited_ms"),
                  "caller": result.detail.get("transaction_last_used_by")}
        if result.state != "given":
            record.update({k: result.detail[k] for k in ("tx_before", "job") if k in result.detail})
            same = (what, key, result.state, result.code, result.message)
            if same == self._last_logged:
                return                                   # the same wait again, every tick: logged once
            self._last_logged = same
        else:
            self._last_logged = None
        self.log(record)

    def _give(self, item_key: int, count: int) -> LiveResult:
        assert self.bridge is not None and self.r3 is not None
        if item_key in self.refused:
            return LiveResult("refused", f"the game refused this item before (code {self.refused[item_key]})",
                              self.refused[item_key])
        if self.table is None:
            self.table = read_item_table(self.bridge, self.base, self.r3["item_table"])
            if self.table is None or self.table.source != "keys":
                self.unsupported = "the game's item list couldn't be read; items arrive when you quit to desktop"
                self.on_event(f"Live delivery off: {self.unsupported}")
                return LiveResult("not now", self.unsupported)
        handle = self.table.handles.get(item_key)
        if handle is None:
            return LiveResult("unknown item", f"the game has no item {item_key}")
        now = int(time.time())
        item_no = LIVE_ITEM_NO_BASE + int(time.time() * 1000) % 1_000_000
        result = self._call(lambda mem: build_gift(stackable_record(handle, count, item_no, now), count, item_no, mem),
                            refusal_key=item_key)
        result.detail.update(handle=hex(handle), item_no=item_no)
        return result

    def give_knowledge(self, key: int, level: int = 1, learn_from: int = LEARN_FROM) -> LiveResult:
        """Learn a knowledge entry (an ability such as Flight, 40002) the way the skill menu does: a change entry
        of kind 4 {key, level, how it was learned} on the game's transaction."""
        if not self.ready():
            return LiveResult("not now", self.unsupported or "the game plugin isn't connected")
        result = self._give_knowledge(key, level, learn_from)
        self._logged("knowledge", key, result, level=level, learn_from=learn_from)
        return result

    def _give_knowledge(self, key: int, level: int, learn_from: int) -> LiveResult:
        assert self.r3 is not None
        try:
            if "knowledge_table" in self.r3:
                # The checker files an unknown key under handle 0xFFFF, which faults later on: look first.
                from .research import knowledge_lookup

                found = knowledge_lookup(self.bridge, self.base, self.r3["knowledge_table"], key)
                if "handle" not in found:
                    return LiveResult("unknown item", f"knowledge {key} isn't in the game's knowledge table "
                                                      f"({found.get('problem', 'no handle')})", detail=found)
            # Not remembered as refused: Flight was refused at some moments and learned at others (r5-knowledge.md),
            # so the engine tries again a few times before leaving it for the save.
            return self._call(lambda mem: build_knowledge_gift(key, level, learn_from, mem), refusal_key=None)
        except BridgeError as exc:
            self.drop(str(exc))
            return LiveResult("not now", f"lost the game plugin ({exc})")

    def _call(self, build, refusal_key) -> LiveResult:
        """One add-check-commit of the game's own with the change entry `build(mem)` makes, on the game-logic
        thread."""
        assert self.bridge is not None and self.r3 is not None
        probe = self._fresh_probe()
        if probe is None:
            if self._await_seen is not None:
                return LiveResult("not now", "the game turned the last item down for now; trying again after its "
                                             "next transaction of its own")
            return LiveResult("not now", "waiting for the game (it pauses while in the background, in a menu or "
                                         "loading)")
        if self.trace:
            # research: watch which of these game functions our call enters (the probe's watch makes room)
            self.bridge.unwatch()
            self.probing = False
            while len(self.bridge.hits(64).records) == 64:
                pass
            for rva in self.trace[:3]:
                self.bridge.watch(self.base + rva, 1, "execute")
        try:
            tx_before = self.bridge.read(probe.tx, TX_STATE).hex()   # kept if the game refuses: its state then
        except BridgeError:
            if not self.bridge.connected:
                raise
            tx_before = None
        if self.mem is None:
            self.mem = self.bridge.alloc(0x200)
        block, layout = build(self.mem)
        self.bridge.poke(self.mem, block)
        now = int(time.time())
        args = [probe.tx, layout["result"], probe.owner, layout["none"], layout["adds"], 1, now, probe.clock,
                self.r3["reason"], 0, 0, 0xFFFF, 1, 1]
        self.bridge.job(self.base + self.r3["give"], args, runner=self.base + self.r3["runner"], tid=probe.tid,
                        set_bytes={probe.tx + 0x1AB: 0})
        start = self.clock()
        status = self.bridge.job_status()
        while status["state"] in ("queued", "running") and self.clock() - start < JOB_WAIT:
            self.sleep(0.1)
            status = self.bridge.job_status()
        if status["state"] == "queued":
            self.bridge.job_cancel()
            status = self.bridge.job_status()
        self.bridge.unwatch()                        # the runner (and the probe: armed again on the next tick)
        self.probing = False
        detail = {"job": status, "tx_before": tx_before, "transaction_last_used_by": probe.caller}
        if self.trace:
            entered = []
            while True:
                got = self.bridge.hits(64)
                entered += [{"fn": r["rip"], "from": (r.get("frames") or ["?"])[0], "tid": r["tid"]}
                            for r in got.records]
                if len(got.records) < 64:
                    break
            detail["trace"] = entered
        if status["state"] == "fault":
            # The game may be in a bad state: stop delivering live until the plugin reconnects.
            self.unsupported = "the game's code faulted during a delivery; reload your last save"
            self.on_event(f"Live delivery stopped: {self.unsupported}")
            return LiveResult("not now", self.unsupported, detail=detail)
        if status["state"] != "done":
            self.probe = None                          # look for the transaction again next time
            return LiveResult("not now", "the game didn't get to it (a menu or loading screen?)", detail=detail)
        code = int.from_bytes(self.bridge.read(layout["result"], 4), "little")
        if code != 0:
            # The game took 8 and 11 items in a row, then turned the next down (code 0x40128a9b, live log of
            # 25 Oct): give it room until it has made an add of its own before trying anything again.
            self._await_seen = self.seen_count
            if refusal_key is not None and (refusal_key in self.accepted or code in BUSY_CODES):
                tries = self.turned_down[refusal_key] = self.turned_down.get(refusal_key, 0) + 1
                if tries < TRANSIENT_TRIES:
                    return LiveResult("not now", f"the game turned it down for now (code {code:#x}); trying again "
                                                 "after its next transaction of its own", code, detail)
            if refusal_key is not None:
                self.refused[refusal_key] = code
            return LiveResult("refused", f"the game refused it (code {code:#x}; transaction last used by "
                                         f"{probe.caller})", code, detail)
        if refusal_key is not None:
            self.accepted.add(refusal_key)
            self.turned_down.pop(refusal_key, None)
        return LiveResult("given", code=0, detail=detail)

    def close(self) -> None:
        if self.bridge is not None:
            try:
                self.bridge.unwatch()
            except BridgeError:
                pass
            self.bridge.close()
            self.bridge = None


def wait_for_transaction(live: LiveChannel, seconds: float = 90.0) -> bool:
    """For the research commands: poll until the game's transaction has been seen (or give up)."""
    start = live.clock()
    while live.clock() - start < seconds:
        live.poll()
        if live.probe is not None and live.clock() - live.probe_seen < PROBE_FRESH:
            return True
        live.sleep(0.5)
    return False
