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

import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, field

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
PROBE_FRESH = 20.0           # a transaction seen longer ago than this is looked for again
JOB_WAIT = 6.0               # seconds the game gets to make a queued call


LEARN_FROM = 25              # "how it was learned", as the saves record story-given knowledge such as Flight


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
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.port, self.on_event, self.bridge_factory = port, on_event, bridge_factory
        self.targets_file, self.clock, self.sleep = targets_file, clock, sleep
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
        self.unsupported = None
        build = load_targets(info.pe_timestamp, self.targets_file)
        if _version(info.plugin_version) < MIN_PLUGIN:
            self.unsupported = (f"the game plugin is {info.plugin_version}; live delivery needs "
                                f"{MIN_PLUGIN[0]}.{MIN_PLUGIN[1]}.0 or newer")
        elif build is None or "r3" not in build or "item_table" not in build["r3"]:
            self.unsupported = (f"live delivery doesn't know this game build yet (build stamp {info.pe_timestamp});"
                                " items arrive when you quit to desktop")
        else:
            self.r3 = {k: int(v, 16) if isinstance(v, str) and v.startswith("0x") else v
                       for k, v in build["r3"].items()}
        self.on_event(f"Game plugin connected ({info.describe()})"
                      + (f": {self.unsupported}" if self.unsupported else "; items arrive while you play"))
        return self.unsupported is None

    def drop(self, why: str = "") -> None:
        if self.bridge is not None:
            try:
                self.bridge.close()
            finally:
                self.bridge = None
                self.on_event("Game plugin disconnected" + (f" ({why})" if why else " (game closed?)"))
        self.probe, self.probing = None, False

    # ------------------------------------------------------------------ the game's transaction
    def _update_probe(self) -> None:
        """Keep a watch on the game's own "add" armed and remember the latest transaction it used. Never waits:
        the game makes such calls by itself every so often, and each client tick reads what came in."""
        assert self.bridge is not None and self.r3 is not None
        if not self.probing:
            while len(self.bridge.hits(8).records) == 8:   # leftovers from earlier watches
                pass
            self.bridge.watch(self.base + self.r3["probe"], 1, "execute")
            self.probing = True
        for r in self.bridge.hits(8).records:
            args = [int(a, 16) for a in r.get("args", [])]
            if len(args) >= 11:
                self.probe = Probe(args[0], args[2], int(r["tid"]), args[9] & 0xFFFFFFFF, args[10] & 0xFFFFFFFF,
                                   (r.get("frames") or ["?"])[0])
                self.probe_seen = self.clock()

    def _fresh_probe(self) -> Probe | None:
        """The game-logic thread's transaction, seen within the last PROBE_FRESH seconds; None while waiting."""
        assert self.bridge is not None
        self._update_probe()
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
            return self._give(item_key, count)
        except BridgeError as exc:
            self.drop(str(exc))
            return LiveResult("not now", f"lost the game plugin ({exc})")

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
        try:
            if ("knowledge", key) in self.refused:
                return LiveResult("refused", "the game refused this before", self.refused[("knowledge", key)])
            return self._call(lambda mem: build_knowledge_gift(key, level, learn_from, mem),
                              refusal_key=("knowledge", key))
        except BridgeError as exc:
            self.drop(str(exc))
            return LiveResult("not now", f"lost the game plugin ({exc})")

    def _call(self, build, refusal_key) -> LiveResult:
        """One add-check-commit of the game's own with the change entry `build(mem)` makes, on the game-logic
        thread."""
        assert self.bridge is not None and self.r3 is not None
        probe = self._fresh_probe()
        if probe is None:
            return LiveResult("not now", "waiting for the game (it pauses while in the background, in a menu or "
                                         "loading)")
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
        detail = {"job": status}
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
            self.refused[refusal_key] = code
            return LiveResult("refused", f"the game refused it (code {code:#x})", code, detail)
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
