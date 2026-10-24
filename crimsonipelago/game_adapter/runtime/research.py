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
    base = int(info.module_base, 16)
    recorded = (report.get("game") or {}).get("pe_timestamp")
    if recorded and recorded != info.pe_timestamp:
        say(f"That report is from another game build ({recorded}, now {info.pe_timestamp}): the addresses "
            "don't match any more. Record a new one with watch-money first.")
        return None
    rvas = addresses_in_report(report)
    if not rvas:
        say("That report has no game addresses in it.")
        return None
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
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"r2-code-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps({
        "plugin": info.plugin_version,
        "game": {"exe_version": info.exe_version, "pe_timestamp": info.pe_timestamp},
        "from_report": report_path.name,
        "functions": list(functions.values()),
    }, indent=1), encoding="utf-8")
    total = sum(len(f["bytes"]) // 2 for f in functions.values())
    say(f"Saved the code of {len(functions)} game functions ({total:,} bytes) on the recorded paths.")
    say(f"Saved: {path}")
    say("Send me that file. It stays between us: it's the game's code, so it doesn't go in the repository.")
    return path


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
            slot, threads = bridge.watch(base + int(f["rva"], 16), 1, "execute")
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
