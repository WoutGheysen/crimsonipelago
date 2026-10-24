"""Guided research with the in-game plugin (R1): find where the game keeps your Copper, then change it live.

Two ways in, used together:

1. **Item numbers.** Every stack the client adds to a save gets an item number from 900,000,001 up
   (save_edit.ITEM_NO_BASE), far from the game's own. If the game keeps that number in memory, the stack's
   amount sits close to it: the client looks for the amount around each of them.
2. **Searching by value**, the way Cheat Engine does: every place holding the amount, then only those that
   follow when the amount changes. Amounts are searched as 4-byte values, which also finds 8-byte ones.

Then each candidate is tried in turn: the client adds 100 copper there, asks whether the bag shows it, and
puts the old amount back when it doesn't (a copy the game only displays, not your money).
"""

from __future__ import annotations

import json
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..save_edit import ITEM_NO_BASE
from .bridge import BridgeError, ResearchBridge

WIDTH = 4                # amounts are searched and written as 4-byte values
ITEM_NO_SPAN = 256       # item numbers 900,000,001 .. 900,000,256
NEAR = 256               # bytes searched on each side of an item number
MAX_CANDIDATES = 16      # the game keeps several copies of the amount (7 in the first test)
CONTEXT = 32


def parse_money(text: str) -> int | None:
    """The amount the game shows, in silver ("8.00", "8", "5,70", "1,234.56"), as copper."""
    text = text.strip().replace(" ", "")
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(",", "")          # 1,234.56: the comma groups thousands
    else:
        text = text.replace(",", ".")         # 5,70: a decimal comma
    try:
        value = round(float(text) * 100)
    except ValueError:
        return None
    return value if value >= 0 else None


def shown(copper: int) -> str:
    return f"{copper / 100:.2f}"


@dataclass
class Candidate:
    addr: int
    source: str
    rank: int            # lower is tried first


def _read(bridge: ResearchBridge, addr: int, size: int) -> bytes | None:
    try:
        return bridge.read(addr, size)
    except BridgeError:
        if not bridge.connected:
            raise
        return None


def _holds(bridge: ResearchBridge, addr: int, amount: int) -> bool:
    data = _read(bridge, addr, WIDTH)
    return data is not None and int.from_bytes(data, "little") == amount


def near_item_numbers(bridge: ResearchBridge, amount: int, say: Callable[[str], None]) -> list[Candidate]:
    """Places holding `amount` within NEAR bytes of an item number the client gave out."""
    report = bridge.scan(ITEM_NO_BASE + 1, ITEM_NO_BASE + ITEM_NO_SPAN, width=8, align=4)
    if report.count == 0:
        say("The item numbers the client gave out are not in memory; searching by amount only.")
        return []
    say(f"{report.count} place(s) hold an item number the client gave out.")
    found: dict[int, Candidate] = {}
    for anchor in report.hits:
        start = max(0, anchor - NEAR)
        data = _read(bridge, start, 2 * NEAR)
        if data is None:
            continue
        number = int.from_bytes(data[anchor - start:anchor - start + 8], "little")
        for off in range(0, len(data) - WIDTH + 1, 4):
            if int.from_bytes(data[off:off + WIDTH], "little") != amount:
                continue
            addr, rel = start + off, start + off - anchor
            if addr not in found or abs(rel) < found[addr].rank:
                found[addr] = Candidate(addr, f"{rel:+d} bytes from item number {number}", abs(rel))
    if found:
        say(f"{len(found)} of them have {shown(amount)} close by.")
    return sorted(found.values(), key=lambda c: (c.rank, c.addr))


def _describe(bridge: ResearchBridge, c: Candidate, say: Callable[[str], None]) -> None:
    say(f"  0x{c.addr:x}: {c.source}")
    data = _read(bridge, c.addr - CONTEXT, 2 * CONTEXT + 8)
    if data is not None:
        say(f"    {data[:CONTEXT].hex()} [{data[CONTEXT:CONTEXT + 8].hex()}] {data[CONTEXT + 8:].hex()}")


def locate_money(bridge: ResearchBridge, ask: Callable[[str], str], say: Callable[[str], None]
                 ) -> tuple[int, list[Candidate]] | None:
    """Asks for the amount, follows it through changes; returns (current amount, places holding it)."""
    say("Open your bag in the game and look at the money. It shows silver: 8.00 means 800 copper.")
    amount = None
    while amount is None:
        amount = parse_money(ask("How much money do you have right now, as the bag shows it (e.g. 8.00)? "))
    say(f"That's {amount} copper.")

    anchored = near_item_numbers(bridge, amount, say)
    report = bridge.scan(amount, width=WIDTH, align=4)
    if report.truncated:
        say(f"Too many places hold {amount} to follow them all. Change your money to a less round amount "
            "(buy or sell something), then start again.")
        return None
    say(f"{report.count} places in memory hold {amount} ({report.ms} ms).")

    changes, previous = 0, None
    while True:
        anchored = [c for c in anchored if _holds(bridge, c.addr, amount)]
        if report.count == 0 and not anchored:
            say("Nothing follows your money any more: the game keeps it in a form this search doesn't see. "
                "Send me this output and crimsonipelago_plugin.log from the bin64 folder.")
            return None
        if (changes >= 2 and report.count <= MAX_CANDIDATES) or (changes >= 1 and anchored):
            break
        if changes >= 3 and report.count <= 64 and report.count == previous:
            break                                   # the same places followed every change: that's all of them
        say("Now change your money in the game: buy or sell something cheap, or pick up coins.")
        text = ask("New amount (as shown), Enter if it hasn't changed, or q to stop: ").strip()
        if text.lower() == "q":
            return None
        new = amount if text == "" else parse_money(text)
        if new is None:
            continue
        if new != amount:
            say(f"That's {new} copper.")
            changes += 1
            previous = report.count
        amount = new
        report = bridge.rescan(amount)
        anchored = [c for c in anchored if _holds(bridge, c.addr, amount)]
        say(f"{report.count} places still match" + (f", {len(anchored)} near an item number." if anchored else "."))

    seen = {c.addr for c in anchored}
    searched = report.hits if report.count <= 64 else []
    candidates = anchored + [Candidate(a, "follows your money", 10**6) for a in searched if a not in seen]
    candidates = [c for c in candidates if _holds(bridge, c.addr, amount)]       # drop copies that moved on
    if not candidates:
        say("The places found changed on their own in the meantime. Start again, without touching your money "
            "while the search runs.")
        return None
    say(f"Found {len(candidates)} candidate(s) after {changes} change(s):")
    for c in candidates:
        _describe(bridge, c, say)
    return amount, candidates


def find_copper(bridge: ResearchBridge, ask: Callable[[str], str] = input, say: Callable[[str], None] = print,
                add: int = 100, wait: Callable[[float], None] = time.sleep) -> bool:
    say("Finding your Copper in the game's memory.")
    found = locate_money(bridge, ask, say)
    if found is None:
        return False
    amount, candidates = found

    target = amount + add
    mode = ask(f"Try them one by one (o), all {len(candidates)} at once (a), or stop (q)? ").strip().lower()
    if mode == "q":
        return False
    if mode == "a":
        return _try_all(bridge, candidates, amount, target, ask, say, wait)

    stack_only = []
    for c in candidates:
        answer = ask(f"Add {add} copper at 0x{c.addr:x}? (y = try it, n = skip, q = stop) ").strip().lower()
        if answer == "q":
            return False
        if answer != "y":
            continue
        written, found = bridge.write(c.addr, amount, target, width=WIDTH)
        if not written:
            say(f"Not written: it holds {found} now, not {amount}. Next one.")
            continue
        # The game copies the real amount into the others (for the screen, the shop...): if they follow
        # within a moment, this one is the source.
        wait(1.0)
        followed = [o.addr for o in candidates if o.addr != c.addr and _holds(bridge, o.addr, target)]
        if followed:
            say(f"{len(followed)} of the other places changed to {target} with it: this looks like the real one.")
        else:
            say("The other places didn't follow (yet).")
        if _yes(ask(f"Close and reopen the bag. Does the money at the top right show {shown(target)}? (y/n) ")):
            say(f"Your Copper lives at 0x{c.addr:x} ({c.source}; {len(followed)} other place(s) followed).")
            _save_test(say, target)
            return True
        if _yes(ask("And the coin stack inside the bag, does that one show the new amount? (y/n) ")):
            stack_only.append(c.addr)
        undone, found = bridge.write(c.addr, target, amount, width=WIDTH)
        say("Put back." if undone else f"The game already changed it to {found} by itself: a copy.")
    if stack_only:
        say("Only the coin stack changed, at " + ", ".join(f"0x{a:x}" for a in stack_only) + ".")
    say("No single place moves the money at the top right. Run it again and choose a (all at once), or send me "
        "this output.")
    return False


def _yes(answer: str) -> bool:
    return answer.strip().lower().startswith("y")


def _save_test(say: Callable[[str], None], target: int) -> None:
    say("Last part: save in the game, quit to the main menu, load that save, and tell me the amount at the top "
        f"right ({shown(target)} means the change stuck). Send me this whole output too.")


def _try_all(bridge: ResearchBridge, candidates: list[Candidate], amount: int, target: int,
             ask: Callable[[str], str], say: Callable[[str], None], wait: Callable[[float], None]) -> bool:
    written = []
    for c in candidates:
        ok, found = bridge.write(c.addr, amount, target, width=WIDTH)
        if ok:
            written.append(c)
        else:
            say(f"  0x{c.addr:x} not written: it holds {found} now.")
    say(f"Wrote {target} to {len(written)} of {len(candidates)} places.")
    wait(1.0)
    top = _yes(ask(f"Close and reopen the bag. Does the money at the top right show {shown(target)}? (y/n) "))
    stack = _yes(ask("And the coin stack inside the bag? (y/n) "))
    say(f"Top right: {'yes' if top else 'no'}; coin stack: {'yes' if stack else 'no'}.")
    if top or stack:
        _save_test(say, target)
        return top
    for c in written:
        bridge.write(c.addr, target, amount, width=WIDTH)
    say("Put them all back. Send me this output.")
    return False


# --------------------------------------------------------------------------- R2: who changes the money
WATCH_SLOTS = 4


def _drain(bridge: ResearchBridge, page: int = 64) -> tuple[list[dict], dict[str, int], int]:
    records, lost = [], 0
    while True:
        got = bridge.hits(page)
        records += got.records
        lost += got.lost
        if len(got.records) < page:
            return records, got.sites, lost


def _record_events(bridge: ResearchBridge, ask: Callable[[str], str], say: Callable[[str], None],
                   suggestions: str, page: int = 64) -> tuple[list[dict], dict[str, int], list[str], int]:
    """Lets the player do things one at a time, labelling what the watches saw with what they did."""
    records: list[dict] = []
    sites: dict[str, int] = {}
    events: list[str] = []
    lost = 0
    _drain(bridge, page)                                     # anything from before the player's actions
    say("Now do these in the game, one at a time. After each one, come back here, type what you did and "
        "press Enter:")
    say("  " + suggestions)
    say("Type d and Enter when you're done.")
    while True:
        bridge.watch_apply()                                 # threads the game started meanwhile
        text = ask("What did you just do (e.g. buy, sell, loot, reward), or d when done: ").strip()
        done = text.lower() in ("d", "q", "done")
        got, sites, dropped = _drain(bridge, page)
        label = "before done" if done else (text or "unlabelled")
        if label in events:                                  # "loot" twice: "loot", then "loot #2"
            n = 2
            while f"{label} #{n}" in events:
                n += 1
            label = f"{label} #{n}"
        for r in got:
            r["event"] = label
        records += got
        lost += dropped
        if got or not done:
            events.append(label)
            say(f"  {label}: {len(got)} record(s)" + (f", {dropped} lost" if dropped else "") + ".")
        if done:
            return records, sites, events, lost


def summarize(records: list[dict], sites: dict[str, int], watched: list[Candidate]) -> list[str]:
    """Readable lines: each writing function, how often, which places, and the call chain of its first hit."""
    by_fn: dict[str, list[dict]] = {}
    for r in records:
        by_fn.setdefault(r.get("fn") or r["rip"], []).append(r)
    lines = []
    for fn, recs in sorted(by_fn.items(), key=lambda kv: -len(kv[1])):
        rips = sorted({r["rip"] for r in recs})
        count = sum(sites.get(rip, 0) for rip in rips) or len(recs)
        places = sorted({f"0x{watched[r['slot']].addr:x}" for r in recs if r["slot"] < len(watched)})
        values = sorted({r["value"] for r in recs})
        lines.append(f"{fn}: {count} write(s) to {', '.join(places)}; values {', '.join(map(str, values[:6]))}")
        chain = recs[0].get("frames", [])[:8]
        if chain:
            lines.append("    called from " + " <- ".join(chain))
    return lines


def _counting_table(bridge: ResearchBridge, addr: int) -> bool:
    """A value in a run like 122, 123, 124: an index table that happens to pass through the amount."""
    data = _read(bridge, addr - 4, 12)
    if data is None:
        return False
    prev, cur, nxt = struct.unpack("<3I", data)
    return abs(prev - cur) == 1 and abs(nxt - cur) == 1


def watch_money(bridge: ResearchBridge, report_dir: Path, ask: Callable[[str], str] = input,
                say: Callable[[str], None] = print, wait: Callable[[float], None] = time.sleep) -> Path | None:
    """R2: watch the places holding the money, let the player change it, and record which code wrote."""
    say("Finding the game code that changes your money. First, where your money is:")
    found = locate_money(bridge, ask, say)
    if found is None:
        return None
    amount, candidates = found
    usable = [c for c in candidates if c.addr % WIDTH == 0
              and (c.rank < 10**6 or not _counting_table(bridge, c.addr))]
    watched = usable[:WATCH_SLOTS]
    records: list[dict] = []
    sites: dict[str, int] = {}
    events: list[str] = []
    lost = 0
    try:
        threads = 0
        for c in watched:
            slot, threads = bridge.watch(c.addr, WIDTH, "write")
            if slot != watched.index(c):
                raise BridgeError(f"plugin gave watch slot {slot} for place {watched.index(c)}")
        say(f"Watching {len(watched)} place(s) on {threads} game threads. If the game stutters or closes, "
            "tell me: the watch uses the same processor feature as a debugger.")
        records, sites, events, lost = _record_events(
            bridge, ask, say, "buy something cheap, sell something, pick up or loot coins, finish a quest that "
            "pays money...")
    finally:
        try:
            bridge.unwatch()
        except BridgeError:
            pass

    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"r2-money-{time.strftime('%Y%m%d-%H%M%S')}.json"
    info = bridge.info
    path.write_text(json.dumps({
        "plugin": info.plugin_version if info else None,
        "game": {"exe_version": info.exe_version, "pe_timestamp": info.pe_timestamp,
                 "module_base": info.module_base} if info else None,
        "amount_at_start": amount,
        "watched": [{"slot": i, "addr": hex(c.addr), "source": c.source} for i, c in enumerate(watched)],
        "events": events,
        "sites": sites,
        "lost": lost,
        "records": records,
    }, indent=1), encoding="utf-8")
    say("")
    if not records:
        say("No writes were seen. Did the money change while watching? Send me the file anyway.")
    else:
        say("What changed your money:")
        for line in summarize(records, sites, watched):
            say("  " + line)
    say(f"Saved: {path}")
    say("Send me that file.")
    return path


# --------------------------------------------------------------------------- R2b: the code on those paths
MODULE = "CrimsonDesert.exe"
MAX_FUNCTION = 16384
FRAMES_PER_RECORD = 12
UNKNOWN_SPAN = 512


def _rva(text: str) -> int | None:
    prefix = MODULE.lower() + "+"
    if not text.lower().startswith(prefix):
        return None
    try:
        return int(text[len(prefix):], 16)
    except ValueError:
        return None


def addresses_in_report(report: dict) -> list[int]:
    """Game addresses (as offsets in the executable) on the recorded paths, nearest to the write first."""
    seen: dict[int, None] = {}
    for r in report.get("records", []):
        for text in [r.get("rip", ""), r.get("fn", "")] + list(r.get("frames", []))[:FRAMES_PER_RECORD]:
            rva = _rva(text)
            if rva is not None:
                seen.setdefault(rva, None)
    for text in report.get("sites", {}):
        rva = _rva(text)
        if rva is not None:
            seen.setdefault(rva, None)
    return list(seen)


def dump_code(bridge: ResearchBridge, report_path: Path, out_dir: Path,
              say: Callable[[str], None] = print) -> Path | None:
    """Saves the game's code for each function on the recorded paths, for analysis outside the game."""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    info = bridge.info
    if info is None:
        raise BridgeError("not connected")
    recorded = (report.get("game") or {}).get("pe_timestamp")
    if recorded and recorded != info.pe_timestamp:
        say(f"That report is from another game build ({recorded}, now {info.pe_timestamp}): the addresses "
            "don't match any more. Record a new one with watch-money first.")
        return None
    rvas = addresses_in_report(report)
    build = load_targets(info.pe_timestamp) if TARGETS_FILE.exists() else None
    for text in (build or {}).get("dump_extra", []):
        rva = int(text, 16)
        if rva not in rvas:
            rvas.append(rva)
    if not rvas:
        say("That report has no game addresses in it.")
        return None
    functions = _dump_functions(bridge, rvas)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"r2-code-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps({
        "plugin": info.plugin_version,
        "game": {"exe_version": info.exe_version, "pe_timestamp": info.pe_timestamp},
        "from_report": report_path.name,
        "functions": functions,
    }, indent=1), encoding="utf-8")
    total = sum(len(f["bytes"]) // 2 for f in functions)
    say(f"Saved the code of {len(functions)} game functions ({total:,} bytes) on the recorded paths.")
    say(f"Saved: {path}")
    say("Send me that file. It stays between us: it's the game's code, so it doesn't go in the repository.")
    return path


def _dump_functions(bridge: ResearchBridge, rvas: list[int]) -> list[dict]:
    info = bridge.info
    assert info is not None
    base = int(info.module_base, 16)
    functions: dict[int, dict] = {}
    for rva in rvas:
        addr = base + rva
        begin, end = bridge.function_bounds(addr)
        if begin == 0:
            begin, end = max(base, addr - UNKNOWN_SPAN), addr + UNKNOWN_SPAN
            kind = "no unwind data: bytes around the address"
        else:
            kind = "function"
        key = begin - base
        if key in functions:
            functions[key]["refs"].append(hex(rva))
            continue
        size = min(end - begin, MAX_FUNCTION)
        data = b""
        while len(data) < size:
            chunk = _read(bridge, begin + len(data), min(4096, size - len(data)))
            if chunk is None:
                break
            data += chunk
        functions[key] = {"rva_begin": hex(key), "rva_end": hex(end - base), "kind": kind,
                          "truncated": end - begin > len(data), "refs": [hex(rva)], "bytes": data.hex()}
    return list(functions.values())


# --------------------------------------------------------------------------- R2c: the calls on those paths
TARGETS_FILE = Path(__file__).with_name("targets.json")
CALL_PAGE = 8          # records with argument memory are large: collect a few at a time


def load_targets(pe_timestamp: int, path: Path = TARGETS_FILE) -> dict | None:
    """The functions to watch for this game build (offsets in the executable), or None if unknown."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("builds", {}).get(str(pe_timestamp))


def watch_calls(bridge: ResearchBridge, report_dir: Path, ask: Callable[[str], str] = input,
                say: Callable[[str], None] = print, targets_file: Path = TARGETS_FILE) -> Path | None:
    """R2c: watch calls of the inventory transaction functions while the player gains and spends things."""
    info = bridge.info
    if info is None:
        raise BridgeError("not connected")
    build = load_targets(info.pe_timestamp, targets_file)
    if build is None:
        say(f"I don't know this game build yet (build stamp {info.pe_timestamp}); the function offsets are "
            "only valid for the build they were found in. Tell me, and I'll redo the search for it.")
        return None
    base = int(info.module_base, 16)
    functions = build["watch_calls"][:WATCH_SLOTS]
    try:
        threads = 0
        for i, f in enumerate(functions):
            slot, threads = bridge.watch(base + int(f["rva"], 16), 1, "execute", follow=f.get("follow"))
            if slot != i:
                raise BridgeError(f"plugin gave watch slot {slot} for function {i}")
        say(f"Watching {len(functions)} game functions on {threads} threads:")
        for f in functions:
            say(f"  {MODULE}+{f['rva']}: {f['name']}")
        records, sites, events, lost = _record_events(
            bridge, ask, say, "loot coins or an item, pick something up, buy, sell, finish a quest that rewards "
            "something...", page=CALL_PAGE)
    finally:
        try:
            bridge.unwatch()
        except BridgeError:
            pass

    for r in records:
        if r["slot"] < len(functions):
            r["function"] = functions[r["slot"]]["name"]
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"r2-calls-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps({
        "plugin": info.plugin_version,
        "game": {"exe_version": info.exe_version, "pe_timestamp": info.pe_timestamp, "module_base": info.module_base},
        "functions": functions,
        "events": events,
        "sites": sites,
        "lost": lost,
        "records": records,
    }, indent=1), encoding="utf-8")
    say("")
    say("Calls seen, per thing you did (the same caller is recorded 8 times at most each time):")
    for event in events:
        counts: dict[str, int] = {}
        for r in records:
            if r.get("event") == event:
                counts[r.get("function", "?")] = counts.get(r.get("function", "?"), 0) + 1
        say(f"  {event}: " + (", ".join(f"{name} x{n}" for name, n in counts.items()) or "none"))
    say(f"Saved: {path}")
    say("Send me that file.")
    return path


# --------------------------------------------------------------------------- R3: give Copper live
RECORD_SIZE = 0xC8          # an item stack record, in the bag and as the item of a change entry
ENTRY_SIZE = 0x38           # a change entry of a transaction
LIVE_ITEM_NO_BASE = 905_000_000   # item numbers of stacks made live (the save editor uses 900,000,001 up)
PROBE_SECONDS = 90
JOB_SECONDS = 15


@dataclass
class Probe:
    tx: int
    owner: int
    tid: int
    unix_time: int
    clock: int
    caller: str


def probe_transaction(bridge: ResearchBridge, add_addr: int, say: Callable[[str], None],
                      wait: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                      seconds: float = PROBE_SECONDS) -> Probe | None:
    """The transaction and owner the game-logic thread uses, from the next call of "add" (the game makes
    some every few seconds by itself)."""
    bridge.watch(add_addr, 1, "execute")
    try:
        start = clock()
        while clock() - start < seconds:
            for r in bridge.hits(8).records:
                args = [int(a, 16) for a in r.get("args", [])]
                if len(args) >= 11:
                    return Probe(args[0], args[2], int(r["tid"]), args[9] & 0xFFFFFFFF, args[10] & 0xFFFFFFFF,
                                 (r.get("frames") or ["?"])[0])
            wait(0.5)
    finally:
        bridge.unwatch()
    return None


def find_stack_record(bridge: ResearchBridge, handle: int, say: Callable[[str], None]) -> tuple[int, bytes] | None:
    """The game-logic side's record of a stack the client added to the save (item numbers from 900,000,001),
    for the item with this key handle."""
    report = bridge.scan(ITEM_NO_BASE + 1, ITEM_NO_BASE + ITEM_NO_SPAN, width=8, align=8)
    found = []
    for addr in report.hits:
        data = _read(bridge, addr, RECORD_SIZE)
        if data is None:
            continue
        key, side = struct.unpack_from("<HxxI", data, 8)
        count = struct.unpack_from("<Q", data, 0x10)[0]
        if key == handle and 0 < count < 10**9:
            found.append((side != 0xFFFFFFFF, addr, data))      # the game-logic side (0xFFFFFFFF) first
    if not found:
        return None
    found.sort()
    say(f"Found your Copper stack ({len(found)} cop{'y' if len(found) == 1 else 'ies'} in memory).")
    return found[0][1], found[0][2]


def build_gift(record: bytes, amount: int, item_no: int, mem: int) -> tuple[bytes, dict[str, int]]:
    """Our memory block: the item (a copy of the stack record with the new amount), one change entry of kind
    0 pointing at it, the list {entries, 1, 1}, an empty list, and the result."""
    item = bytearray(record[:RECORD_SIZE])
    struct.pack_into("<Q", item, 0, item_no)
    struct.pack_into("<Q", item, 0x10, amount)
    layout = {"item": mem, "entry": mem + 0x100, "adds": mem + 0x140, "none": mem + 0x160, "result": mem + 0x180}
    block = bytearray(0x200)
    block[0:RECORD_SIZE] = item
    entry = bytearray(ENTRY_SIZE)                          # kind 0 at +0, -1 at +8, the item at +0x30
    struct.pack_into("<Q", entry, 0x08, 0xFFFFFFFFFFFFFFFF)
    struct.pack_into("<Q", entry, 0x30, layout["item"])
    block[0x100:0x100 + ENTRY_SIZE] = entry
    struct.pack_into("<QII", block, 0x140, layout["entry"], 1, 1)
    struct.pack_into("<QII", block, 0x160, 0, 0, 0)
    struct.pack_into("<I", block, 0x180, 0xFFFFFFFF)       # the game writes 0 here when the change worked
    return bytes(block), layout


# --------------------------------------------------------------------------- R4: any item
TABLE_COUNT, TABLE_KEYS, TABLE_DATA = 0x08, 0x28, 0x58     # fields of the game's item table
LIVE_ITEM_NO_END = LIVE_ITEM_NO_BASE + 999_999


@dataclass
class ItemTable:
    handles: dict[int, int]        # item key (as in the save) -> the game's 16-bit handle
    count: int
    loaded: int                    # entries whose item data is already in memory
    checked: int                   # loaded entries whose key matched the table's key column
    source: str                    # "keys" (the table's key column) or "loaded" (item data only)


def read_item_table(bridge: ResearchBridge, base: int, table_rva: int) -> ItemTable | None:
    """The game's item table (found through its lookup function, +0x38ab60): a count, a column of
    {key, location} pairs, and pointers to item data loaded on first use (key in its first 4 bytes)."""
    table = int.from_bytes(_read(bridge, base + table_rva, 8) or b"\0" * 8, "little")
    head = _read(bridge, table, 0x60) if table else None
    if head is None:
        return None
    count = struct.unpack_from("<I", head, TABLE_COUNT)[0]
    keys_at, data_at = struct.unpack_from("<Q", head, TABLE_KEYS)[0], struct.unpack_from("<Q", head, TABLE_DATA)[0]
    if not 0 < count < 1_000_000:
        return None

    def array(addr: int) -> bytes:
        out = b""
        while len(out) < count * 8:
            chunk = _read(bridge, addr + len(out), min(4096, count * 8 - len(out)))
            if chunk is None:
                break
            out += chunk
        return out

    keys, data = array(keys_at), array(data_at)
    column = {i: struct.unpack_from("<I", keys, 8 * i)[0] for i in range(len(keys) // 8)}
    loaded = {i: struct.unpack_from("<Q", data, 8 * i)[0] for i in range(len(data) // 8)}
    loaded = {i: p for i, p in loaded.items() if p}
    from_data: dict[int, int] = {}
    for i, p in loaded.items():
        raw = _read(bridge, p, 4)
        if raw is not None:
            from_data[i] = struct.unpack_from("<I", raw)[0]
    checked = sum(1 for i, k in from_data.items() if column.get(i) == k)
    if from_data and checked == len(from_data):
        return ItemTable({k: i for i, k in column.items()}, count, len(loaded), checked, "keys")
    return ItemTable({k: i for i, k in from_data.items()}, count, len(loaded), checked, "loaded")


@dataclass
class GiveResult:
    state: str                     # done, fault, not run, unknown item, no template
    code: int | None = None        # the game's result code (0: accepted)
    detail: dict | None = None


class LiveGiver:
    """Gives items while the game runs: one add-check-commit of the game's own per item, made on the
    game-logic thread between two of its tasks. Finds what it needs once and reuses it."""

    def __init__(self, bridge: ResearchBridge, r3: dict, wait: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        info = bridge.info
        if info is None:
            raise BridgeError("not connected")
        self.bridge, self.r3, self.wait, self.clock = bridge, r3, wait, clock
        self.base = int(info.module_base, 16)
        self.probe: Probe | None = None
        self.table: ItemTable | None = None
        self.templates: dict[int, tuple[int, bytes]] = {}       # handle -> (address, record)
        self.mem: int | None = None

    def find_transaction(self, say: Callable[[str], None]) -> Probe | None:
        self.probe = probe_transaction(self.bridge, self.base + self.r3["probe"], say, self.wait, self.clock)
        if self.probe is not None:
            head = _read(self.bridge, self.probe.tx, 8)
            if head is None or int.from_bytes(head, "little") not in (self.probe.owner, 0):
                say("   the transaction didn't look right (its first field isn't the owner).")
                self.probe = None
        else:
            say("   the game didn't use its transaction within 90 seconds.")
        return self.probe

    def load_table(self) -> ItemTable | None:
        if "item_table" in self.r3:
            self.table = read_item_table(self.bridge, self.base, self.r3["item_table"])
        return self.table

    def find_templates(self) -> dict[int, tuple[int, bytes]]:
        """Item records next to the item numbers the client handed out in the save (900,000,001 up). The
        search stays narrow: the plugin returns the first 64 places only, and a wide range of numbers
        turns up thousands of unrelated ones first."""
        report = self.bridge.scan(ITEM_NO_BASE + 1, ITEM_NO_BASE + ITEM_NO_SPAN, width=8, align=8)
        templates: dict[int, list[tuple[bool, int, bytes]]] = {}
        for addr in report.hits:
            data = _read(self.bridge, addr, RECORD_SIZE)
            if data is None:
                continue
            number = struct.unpack_from("<Q", data, 0)[0]
            if not (ITEM_NO_BASE < number <= ITEM_NO_BASE + ITEM_NO_SPAN or LIVE_ITEM_NO_BASE <= number <= LIVE_ITEM_NO_END):
                continue
            handle, side = struct.unpack_from("<HxxI", data, 8)
            count = struct.unpack_from("<Q", data, 0x10)[0]
            if 0 < count < 10**9:
                templates.setdefault(handle, []).append((side != 0xFFFFFFFF, addr, data))
        self.templates = {h: (sorted(v)[0][1], sorted(v)[0][2]) for h, v in templates.items()}
        return self.templates

    def template_for(self, handle: int) -> tuple[int, bytes] | None:
        """A record of the same item if there is one, else Copper's, else any."""
        for h in (handle, self.r3.get("copper_handle")):
            if h in self.templates:
                return self.templates[h]
        return next(iter(self.templates.values()), None)

    def give(self, key: int, count: int, reason: int | None = None) -> GiveResult:
        if self.probe is None:
            return GiveResult("not run", detail={"why": "no transaction"})
        handle = self.table.handles.get(key) if self.table else (self.r3.get("copper_handle") if key == 1 else None)
        if handle is None:
            return GiveResult("unknown item", detail={"key": key})
        template = self.template_for(handle)
        if template is None:
            return GiveResult("no template")
        record = bytearray(template[1])
        same_item = struct.unpack_from("<H", record, 8)[0] == handle
        struct.pack_into("<H", record, 8, handle)
        if self.mem is None:
            self.mem = self.bridge.alloc(0x200)
        item_no = LIVE_ITEM_NO_BASE + int(time.time() * 1000) % 1_000_000
        block, layout = build_gift(bytes(record), count, item_no, self.mem)
        self.bridge.poke(self.mem, block)
        p = self.probe
        args = [p.tx, layout["result"], p.owner, layout["none"], layout["adds"], 1, int(time.time()), p.clock,
                self.r3["reason"] if reason is None else reason, 0, 0, 0xFFFF, 1, 1]
        self.bridge.job(self.base + self.r3["give"], args, runner=self.base + self.r3["runner"], tid=p.tid,
                        set_bytes={p.tx + 0x1AB: 0})
        start = self.clock()
        status = self.bridge.job_status()
        while status["state"] in ("queued", "running") and self.clock() - start < JOB_SECONDS:
            self.wait(0.2)
            status = self.bridge.job_status()
        if status["state"] == "queued":
            self.bridge.job_cancel()
            status = self.bridge.job_status()
        result = _read(self.bridge, layout["result"], 4)
        code = int.from_bytes(result, "little") if result else None
        detail = {"key": key, "handle": hex(handle), "count": count, "item_no": item_no, "template": hex(template[0]),
                  "template_same_item": same_item, "args": [hex(a) for a in args], "job": status}
        state = status["state"] if status["state"] in ("done", "fault") else "not run"
        self.bridge.unwatch()                       # the runner: the game's tasks run without it until the next
        return GiveResult(state, code, detail)

    def close(self) -> None:
        try:
            self.bridge.unwatch()
        except BridgeError:
            pass


def give_item(bridge: ResearchBridge, report_dir: Path, key: int, count: int, ask: Callable[[str], str] = input,
              say: Callable[[str], None] = print, wait: Callable[[float], None] = time.sleep,
              clock: Callable[[], float] = time.monotonic, targets_file: Path = TARGETS_FILE,
              label: str | None = None) -> Path | None:
    """R4: give `count` of the item with this key (as in the save) while the game runs."""
    info = bridge.info
    if info is None:
        raise BridgeError("not connected")
    build = load_targets(info.pe_timestamp, targets_file)
    if build is None or "r3" not in build:
        say(f"I don't know this game build yet (build stamp {info.pe_timestamp}). Tell me.")
        return None
    r3 = {k: int(v, 16) if isinstance(v, str) and v.startswith("0x") else v for k, v in build["r3"].items()}
    label = label or f"item {key}"
    report: dict = {"plugin": info.plugin_version, "game": {"exe_version": info.exe_version,
                    "pe_timestamp": info.pe_timestamp, "module_base": info.module_base},
                    "key": key, "count": count}
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"r4-give-{time.strftime('%Y%m%d-%H%M%S')}.json"

    def save(outcome: str) -> Path:
        report["outcome"] = outcome
        path.write_text(json.dumps(report, indent=1), encoding="utf-8")
        return path

    say(f"Giving you {count} x {label} while you play.")
    say("Use your test playthrough. Make a manual save first, so you can go back to it.")
    if not _yes(ask("Saved, and standing somewhere quiet (not in a menu)? (y/n) ")):
        return None
    giver = LiveGiver(bridge, r3, wait, clock)
    try:
        say("1/4 Waiting for the game to use its own transaction (walk around; picking something up makes it "
            "happen at once)...")
        probe = giver.find_transaction(say)
        if probe is None:
            say("Run this again and pick something up (a plant, a coin) while it waits.")
            return save("no transaction seen")
        report["probe"] = {"tx": hex(probe.tx), "owner": hex(probe.owner), "tid": probe.tid, "clock": probe.clock}
        say(f"   transaction found (thread {probe.tid}).")

        say("2/4 Reading the game's item list...")
        table = giver.load_table()
        if table is not None:
            report["table"] = {"count": table.count, "loaded": table.loaded, "checked": table.checked,
                               "source": table.source}
            say(f"   {table.count} items, {table.loaded} loaded; key column "
                + ("confirmed" if table.source == "keys" else f"not confirmed ({table.checked} of {table.loaded})"))
        if key not in (table.handles if table else {1: 0}):
            say(f"The game's item list has no item {key}" + ("" if table and table.source == "keys" else
                " among the items loaded so far") + ". Check the key, or pick one up once and try again.")
            return save("unknown item")

        say("3/4 Finding an item record to copy...")
        giver.find_templates()
        report["templates"] = {hex(h): hex(a) for h, (a, _) in giver.templates.items()}
        if not giver.templates:
            say("No item from the client's own deliveries is in memory; this needs one (the first seed's "
                "playthrough has some).")
            return save("no template")
        if not _yes(ask(f"Give {count} x {label} now? (y/n) ")):
            return save("cancelled before the call")

        say("4/4 Asking the game to do it, between two of its own tasks...")
        result = giver.give(key, count)
        report["give"] = {"state": result.state, "code": result.code, **(result.detail or {})}
    finally:
        giver.close()

    if result.state == "fault":
        say(f"The game's code hit a fault during the call (exception 0x{result.detail['job']['code']:08x}). "
            "Don't save: quit to the main menu or desktop and load your manual save.")
        save("fault")
    elif result.state != "done":
        say("The game didn't get to it in time (cancelled; nothing was changed).")
        save("not run")
    elif result.code != 0:
        say(f"The game refused the change (result code {result.code}); nothing was added.")
        save("refused")
    else:
        say("The game accepted it.")
        report["player"] = {
            "bag": _yes(ask(f"Is it in your bag ({count} x {label})? (y/n) ")),
            "popup": _yes(ask("Did the game show its message for it? (y/n) ")),
        }
        say("Last part: save in the game, quit to the main menu, load that save, and check it's still there.")
        save("accepted")
    say(f"Saved: {path}")
    say("Send me that file.")
    return path


def item_table(bridge: ResearchBridge, report_dir: Path, say: Callable[[str], None] = print,
               targets_file: Path = TARGETS_FILE) -> Path | None:
    """Saves the game's item keys and handles (numbers only), to check the table is read right."""
    info = bridge.info
    if info is None:
        raise BridgeError("not connected")
    build = load_targets(info.pe_timestamp, targets_file)
    if build is None or "item_table" not in build.get("r3", {}):
        say(f"I don't know this game build yet (build stamp {info.pe_timestamp}).")
        return None
    table = read_item_table(bridge, int(info.module_base, 16), int(build["r3"]["item_table"], 16))
    if table is None:
        say("The item table isn't where I expected it.")
        return None
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"r4-items-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps({"game": {"pe_timestamp": info.pe_timestamp}, "count": table.count,
                                "loaded": table.loaded, "checked": table.checked, "source": table.source,
                                "handles": {str(k): v for k, v in sorted(table.handles.items())}}, indent=1),
                    encoding="utf-8")
    say(f"{table.count} items; {table.loaded} loaded, {table.checked} of those match the key column "
        f"({'confirmed' if table.source == 'keys' else 'NOT confirmed'}).")
    for key, name in ((1, "Copper"), (75001, "Abyss Artifact")):
        say(f"  {name} ({key}): handle {hex(table.handles[key]) if key in table.handles else 'not found'}")
    say(f"Saved: {path}")
    return path


def give_copper(bridge: ResearchBridge, report_dir: Path, ask: Callable[[str], str] = input,
                say: Callable[[str], None] = print, amount: int = 100,
                wait: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                targets_file: Path = TARGETS_FILE) -> Path | None:
    """R3: give `amount` copper while the game runs, through the game's own add-check-commit, on the
    game-logic thread."""
    info = bridge.info
    if info is None:
        raise BridgeError("not connected")
    build = load_targets(info.pe_timestamp, targets_file)
    if build is None or "r3" not in build:
        say(f"I don't know this game build yet (build stamp {info.pe_timestamp}). Tell me.")
        return None
    r3 = {k: int(v, 16) if isinstance(v, str) and v.startswith("0x") else v for k, v in build["r3"].items()}
    base = int(info.module_base, 16)
    report: dict = {"plugin": info.plugin_version, "game": {"exe_version": info.exe_version,
                    "pe_timestamp": info.pe_timestamp, "module_base": info.module_base}, "amount": amount}
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"r3-give-{time.strftime('%Y%m%d-%H%M%S')}.json"

    def save(outcome: str) -> Path:
        report["outcome"] = outcome
        path.write_text(json.dumps(report, indent=1), encoding="utf-8")
        return path

    say(f"R3: giving you {shown(amount)} silver ({amount} copper) while you play, the way looting does.")
    say("Use your test playthrough. Make a manual save first, so you can go back to it.")
    if not _yes(ask("Saved, and standing somewhere quiet (not in a menu)? (y/n) ")):
        return None

    say("1/4 Waiting for the game to use its own transaction (keep playing: walk around)...")
    probe = probe_transaction(bridge, base + r3["probe"], say, wait, clock)
    if probe is None:
        say("The game didn't use it in time. Pick something up, then run this again.")
        save("no transaction seen")
        return path
    report["probe"] = {"tx": hex(probe.tx), "owner": hex(probe.owner), "tid": probe.tid,
                       "unix_time": probe.unix_time, "clock": probe.clock, "caller": probe.caller}
    head = _read(bridge, probe.tx, 8)
    if head is None or int.from_bytes(head, "little") not in (probe.owner, 0):
        say("That doesn't look like the transaction I expected (its first field isn't the owner). Stopping.")
        save("transaction check failed")
        return path
    say(f"   transaction found (thread {probe.tid}).")

    say("2/4 Finding your Copper stack...")
    found = find_stack_record(bridge, r3["copper_handle"], say)
    if found is None:
        say("No Copper stack from the client's own deliveries is in memory. This test needs one: get Copper "
            "through an Archipelago delivery first (or use the playthrough from the first seed).")
        save("no copper stack")
        return path
    record_addr, record = found
    before = struct.unpack_from("<Q", record, 0x10)[0]
    report["record"] = {"addr": hex(record_addr), "bytes": record.hex(), "count_before": before}
    pointers = [hex(off) for off in range(8, RECORD_SIZE, 8)                 # +0 is the item number
                if 0x10000 < struct.unpack_from("<Q", record, off)[0] < 0x7FFFFFFFFFFF]
    report["record"]["pointer_like_fields"] = pointers
    say(f"   {shown(before)} silver in it.")

    say("3/4 Preparing the change...")
    mem = bridge.alloc(0x200)
    item_no = LIVE_ITEM_NO_BASE + int(time.time()) % 1_000_000
    block, layout = build_gift(record, amount, item_no, mem)
    bridge.poke(mem, block)
    unix_now = int(time.time())
    args = [probe.tx, layout["result"], probe.owner, layout["none"], layout["adds"], 1, unix_now, probe.clock,
            r3["reason"], 0, 0, 0xFFFF, 1, 1]
    report["call"] = {"fn": hex(base + r3["give"]), "args": [hex(a) for a in args],
                      "layout": {k: hex(v) for k, v in layout.items()}, "item_no": item_no,
                      "set": {hex(probe.tx + 0x1AB): 0}}
    if not _yes(ask(f"Give {amount} copper now? (y/n) ")):
        save("cancelled before the call")
        return path

    say("4/4 Asking the game to do it, between two of its own tasks...")
    bridge.job(base + r3["give"], args, runner=base + r3["runner"], tid=probe.tid, set_bytes={probe.tx + 0x1AB: 0})
    start = clock()
    status = bridge.job_status()
    while status["state"] in ("queued", "running") and clock() - start < JOB_SECONDS:
        wait(0.2)
        status = bridge.job_status()
    if status["state"] == "queued":
        bridge.job_cancel()
        status = bridge.job_status()
    bridge.unwatch()
    report["job"] = status
    result = _read(bridge, layout["result"], 4)
    code = int.from_bytes(result, "little") if result else None
    after_data = _read(bridge, record_addr + 0x10, 8)
    after = int.from_bytes(after_data, "little") if after_data else None
    report["result_code"] = code
    report["count_after"] = after

    if status["state"] == "fault":
        say(f"The game's code hit a fault during the call (exception 0x{status['code']:08x}). Don't save: quit "
            "to the main menu or desktop and load your manual save.")
        save("fault")
    elif status["state"] != "done":
        say("The game didn't get to it in time (cancelled; nothing was changed).")
        save("not run")
    elif code != 0:
        say(f"The game refused the change (result code {code}); nothing was added.")
        save("refused")
    else:
        say(f"The game accepted it: your stack went from {shown(before)} to {shown(after or 0)} silver.")
        saw = _yes(ask(f"Does your bag show it (about {shown(after or before + amount)} silver)? (y/n) "))
        top = _yes(ask("And the money at the top right, did that change too? (y/n) "))
        popup = _yes(ask("Did the game show a message or popup for it? (y/n) "))
        report["player"] = {"bag": saw, "top_right": top, "popup": popup}
        say("Last part: save in the game, quit to the main menu, load that save, and tell me the amount.")
        save("accepted")

    # The helper functions' code, for what comes next (stays on your PC and in our conversation).
    extra = [int(t, 16) for t in build.get("dump_extra", [])]
    if extra:
        code_path = report_dir / f"r2-code-{time.strftime('%Y%m%d-%H%M%S')}.json"
        code_path.write_text(json.dumps({"plugin": info.plugin_version, "game": report["game"],
                                         "from_report": path.name,
                                         "functions": _dump_functions(bridge, extra)}, indent=1), encoding="utf-8")
        say(f"Saved: {code_path}")
    say(f"Saved: {path}")
    say("Send me both files.")
    return path


# --------------------------------------------------------------------------- R5: knowledge (abilities) live
def give_knowledge(bridge: ResearchBridge, report_dir: Path, key: int, level: int = 1,
                   ask: Callable[[str], str] = input, say: Callable[[str], None] = print,
                   wait: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                   targets_file: Path = TARGETS_FILE, learn_from: int | None = None,
                   trace: list[int] | None = None) -> Path | None:
    """R5: learn a knowledge entry (Flight is 40002) while the game runs, as the skill menu does."""
    from .live import LEARN_FROM, LiveChannel, wait_for_transaction

    live = LiveChannel(on_event=say, targets_file=targets_file, clock=clock, sleep=wait)
    say(f"Learning knowledge {key} (level {level}) while you play" + (" (Flight)" if key == 40002 else "") + ".")
    say("Use a save that doesn't have it yet, and make a manual save first.")
    if not _yes(ask("Saved, and standing somewhere quiet (not in a menu)? (y/n) ")):
        return None
    if not live.attach(bridge):
        return None
    live.trace = list(trace or [])
    report: dict = {"plugin": bridge.info.plugin_version if bridge.info else None, "key": key, "level": level,
                    "trace": [hex(t) for t in live.trace]}
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"r5-learn-{time.strftime('%Y%m%d-%H%M%S')}.json"
    try:
        say("1/2 Waiting for the game to use its own transaction (walk around, or pick something up)...")
        if not wait_for_transaction(live):
            say("The game didn't use it within 90 seconds. Run this again and pick something up while it waits.")
            report["outcome"] = "no transaction seen"
            path.write_text(json.dumps(report, indent=1), encoding="utf-8")
            return path
        probe = live.probe
        if probe is not None:
            tx = _read(bridge, probe.tx, 0x1C0)
            report["probe"] = {"caller": probe.caller, "tid": probe.tid, "tx": hex(probe.tx), "owner": hex(probe.owner),
                               "tx_bytes": tx.hex() if tx else None}
            say(f"   the game's transaction, last used by {probe.caller}")
        say("2/2 Asking the game to learn it, between two of its own tasks...")
        result = live.give_knowledge(key, level, LEARN_FROM if learn_from is None else learn_from)
        report["result"] = {"state": result.state, "code": result.code, "message": result.message, **result.detail}
    finally:
        try:
            bridge.unwatch()
        except BridgeError:
            pass
    if result.given:
        say("The game accepted it.")
        question = "Can you glide now?" if key == 40002 else "Do you have it now (check the skill menu)?"
        report["player"] = {"has_it": _yes(ask(f"{question} (y/n) ")),
                            "popup": _yes(ask("Did the game show a message for it? (y/n) "))}
        say("Last part: save, quit to the main menu, load that save, and check you still have it.")
        report["outcome"] = "accepted"
    else:
        say(f"Not learned: {result.message}")
        if "trace" in result.detail:
            entered = sorted({t["fn"] for t in result.detail["trace"]})
            say("   the call went through: " + (", ".join(entered) if entered else "none of the watched functions"))
        fault = (result.detail.get("job") or {}).get("fault")
        if fault:
            say(f"   the fault: {fault.get('rip')} in {fault.get('fn') or '?'}, "
                f"{'writing' if fault.get('access') == 1 else 'reading'} {fault.get('addr')}")
        report["outcome"] = result.state
    path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    say(f"Saved: {path}")
    say("Send me that file.")
    return path


KNOWLEDGE_BUCKET = 0x100     # the knowledge table's hash buckets: a 4-byte count, then {u32 key, u32 index} pairs


def knowledge_lookup(bridge: ResearchBridge, base: int, table_rva: int, key: int) -> dict:
    """Look `key` up the way the transaction's checker does for a kind-4 entry (+0x22ce807): the global at
    `table_rva` -> a map with the bucket count at +0x68, buckets at +0x78, records at +0x80; a record holds the
    key at +4 and the 16-bit handle at +8. Read only."""
    out: dict = {"key": key}
    table = int.from_bytes(_read(bridge, base + table_rva, 8) or bytes(8), "little")
    head = _read(bridge, table, 0x90) if table else None
    if head is None:
        out["problem"] = "table not readable"
        return out
    out["used"] = struct.unpack_from("<I", head, 0x6C)[0]
    buckets = struct.unpack_from("<I", head, 0x68)[0]
    bucket_base, records = struct.unpack_from("<QQ", head, 0x78)
    out.update(buckets=buckets)
    if not buckets:
        out["problem"] = "no buckets"
        return out
    bucket = _read(bridge, bucket_base + (key % buckets) * KNOWLEDGE_BUCKET, KNOWLEDGE_BUCKET)
    if bucket is None:
        out["problem"] = "bucket not readable"
        return out
    count = struct.unpack_from("<I", bucket, 0)[0]
    out["bucket_entries"] = count
    for i in range(min(count, (KNOWLEDGE_BUCKET - 8) // 8)):
        k, index = struct.unpack_from("<II", bucket, 8 + 8 * i)
        if k != key:
            continue
        rec_ptr = int.from_bytes(_read(bridge, records + 8 * index, 8) or bytes(8), "little")
        rec = _read(bridge, rec_ptr, 0x40) if rec_ptr else None
        out.update(index=index, record=rec.hex() if rec else None)
        if rec is not None and struct.unpack_from("<I", rec, 4)[0] == key:
            out["handle"] = struct.unpack_from("<H", rec, 8)[0]
        return out
    out["problem"] = "not in the table"
    return out


def knowledge_check(bridge: ResearchBridge, report_dir: Path, keys: list[int], say: Callable[[str], None] = print,
                    targets_file: Path = TARGETS_FILE) -> Path | None:
    """R5: is a knowledge key in the game's knowledge table (read only), plus the code of the functions that
    learn it, for the Flight fault."""
    info = bridge.info
    if info is None:
        raise BridgeError("not connected")
    build = load_targets(info.pe_timestamp, targets_file)
    if build is None or "knowledge_table" not in build.get("r3", {}):
        say(f"I don't know this game build yet (build stamp {info.pe_timestamp}).")
        return None
    base = int(info.module_base, 16)
    table_rva = int(build["r3"]["knowledge_table"], 16)
    found = [knowledge_lookup(bridge, base, table_rva, k) for k in keys]
    for f in found:
        say(f"  {f['key']}: " + (f"handle {f['handle']:#x}" if "handle" in f else f["problem"] if "problem" in f
                                 else "found, but the record doesn't match"))
    extra = [int(t, 16) for t in build.get("r5_dump", [])]
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"r5-knowledge-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps({"game": {"pe_timestamp": info.pe_timestamp}, "lookups": found}, indent=1),
                    encoding="utf-8")
    if extra:
        code_path = report_dir / f"r2-code-{time.strftime('%Y%m%d-%H%M%S')}.json"
        code_path.write_text(json.dumps({"plugin": info.plugin_version, "game": {"pe_timestamp": info.pe_timestamp},
                                         "from_report": path.name, "functions": _dump_functions(bridge, extra)},
                                        indent=1), encoding="utf-8")
        say(f"Saved: {code_path}")
    say(f"Saved: {path}")
    say("Send me both files.")
    return path


def error_sites(bridge: ResearchBridge, report_dir: Path, code: int, say: Callable[[str], None] = print) -> Path | None:
    """Where the game produces a result code: every `mov r32, [rip+disp]` in the code dumps saved so far
    (r2-code-*.json) whose global holds `code` right now. Read only."""
    info = bridge.info
    if info is None:
        raise BridgeError("not connected")
    base = int(info.module_base, 16)
    dumps = sorted(report_dir.glob("r2-code-*.json"))
    seen: dict[int, None] = {}
    sites = []
    for dump in dumps:
        try:
            doc = json.loads(dump.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (doc.get("game") or {}).get("pe_timestamp") not in (None, info.pe_timestamp):
            continue
        for f in doc.get("functions", []):
            start = int(f["rva_begin"], 16)
            data = bytes.fromhex(f["bytes"])
            for i in range(len(data) - 6):
                rex = 1 if data[i] in (0x44, 0x45, 0x4C) and i + 7 <= len(data) else 0
                op, modrm = data[i + rex], data[i + rex + 1]
                if op != 0x8B or modrm & 0xC7 != 0x05:
                    continue
                disp = struct.unpack_from("<i", data, i + rex + 2)[0]
                target = start + i + rex + 6 + disp
                if target in seen:
                    if seen[target] is not None:
                        sites.append({"site": hex(start + i), "global": hex(target), "function": f["rva_begin"]})
                    continue
                raw = _read(bridge, base + target, 4)
                hit = raw is not None and int.from_bytes(raw, "little") == code
                seen[target] = True if hit else None
                if hit:
                    sites.append({"site": hex(start + i), "global": hex(target), "function": f["rva_begin"]})
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"r5-errors-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps({"code": hex(code), "dumps": [d.name for d in dumps], "sites": sites}, indent=1),
                    encoding="utf-8")
    say(f"{len(sites)} place(s) in the saved code load result code {code:#x}:")
    for site in sites[:20]:
        say(f"  CrimsonDesert.exe+{site['site'][2:]} (function +{site['function'][2:]})")
    say(f"Saved: {path}")
    return path
