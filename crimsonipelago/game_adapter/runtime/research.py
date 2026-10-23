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

from collections.abc import Callable
from dataclasses import dataclass

from ..save_edit import ITEM_NO_BASE
from .bridge import BridgeError, ResearchBridge

WIDTH = 4                # amounts are searched and written as 4-byte values
ITEM_NO_SPAN = 256       # item numbers 900,000,001 .. 900,000,256
NEAR = 256               # bytes searched on each side of an item number
MAX_CANDIDATES = 6
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


def find_copper(bridge: ResearchBridge, ask: Callable[[str], str] = input, say: Callable[[str], None] = print,
                add: int = 100) -> bool:
    say("Finding your Copper in the game's memory.")
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
        return False
    say(f"{report.count} places in memory hold {amount} ({report.ms} ms).")

    changes = 0
    while True:
        anchored = [c for c in anchored if _holds(bridge, c.addr, amount)]
        if report.count == 0 and not anchored:
            say("Nothing follows your money any more: the game keeps it in a form this search doesn't see. "
                "Send me this output and crimsonipelago_plugin.log from the bin64 folder.")
            return False
        if (changes >= 2 and report.count <= MAX_CANDIDATES) or (changes >= 1 and anchored):
            break
        say("Now change your money in the game: buy or sell something cheap, or pick up coins.")
        text = ask("New amount (as shown), Enter if it hasn't changed, or q to stop: ").strip()
        if text.lower() == "q":
            return False
        new = amount if text == "" else parse_money(text)
        if new is None:
            continue
        if new != amount:
            say(f"That's {new} copper.")
        if new != amount:
            changes += 1
        amount = new
        report = bridge.rescan(amount)
        anchored = [c for c in anchored if _holds(bridge, c.addr, amount)]
        say(f"{report.count} places still match" + (f", {len(anchored)} near an item number." if anchored else "."))

    seen = {c.addr for c in anchored}
    searched = report.hits if report.count <= MAX_CANDIDATES else []
    candidates = anchored + [Candidate(a, "follows your money", 10**6) for a in searched if a not in seen]
    candidates = [c for c in candidates if _holds(bridge, c.addr, amount)]       # drop copies that moved on
    if not candidates:
        say("The places found changed on their own in the meantime. Start again, without touching your money "
            "while the search runs.")
        return False
    say(f"Found {len(candidates)} candidate(s) after {changes} change(s):")
    for c in candidates:
        _describe(bridge, c, say)

    target = amount + add
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
        answer = ask(f"Close and reopen the bag. Does it show {shown(target)} now? (y/n) ").strip().lower()
        if answer == "y":
            say(f"Your Copper lives at 0x{c.addr:x} ({c.source}).")
            say("Last part: save in the game, quit to the main menu, load that save, and tell me the amount "
                f"it shows ({shown(target)} means the change stuck). Send me this whole output too.")
            return True
        undone, found = bridge.write(c.addr, target, amount, width=WIDTH)
        say("Put back: that one isn't your money." if undone else
            f"The game already changed it to {found} by itself: a copy, not your money.")
    say("None of the candidates is your money. Send me this output and crimsonipelago_plugin.log from bin64.")
    return False
