"""Guided research with the in-game plugin (R1): find where the game keeps your Copper, then change it live.

Copper is item key 1. In the save, its stack count sits 8 bytes after the item key's 4 bytes plus the slot
(itemKey u32, slotNo u16, stackCount u64); the running game may lay it out differently, so candidates are
shown with the bytes around them and ranked by whether the item key 1 sits nearby.
"""

from __future__ import annotations

import struct
from collections.abc import Callable

from .bridge import ResearchBridge

COPPER_KEY = 1
CONTEXT = 64


def parse_money(text: str) -> int | None:
    """The amount the game shows ("8.00" silver, or "800" copper) as copper."""
    text = text.strip().replace(",", ".")
    if not text:
        return None
    try:
        if "." in text:
            return round(float(text) * 100)
        return int(text)
    except ValueError:
        return None


def nearby_key(data: bytes, centre: int, key: int = COPPER_KEY) -> int | None:
    """Offset (relative to the value) of the closest 4-byte aligned u32 equal to `key`, if any."""
    best = None
    for off in range(0, len(data) - 3, 4):
        if off == centre or struct.unpack_from("<I", data, off)[0] != key:
            continue
        rel = off - centre
        if best is None or abs(rel) < abs(best):
            best = rel
    return best


def find_copper(bridge: ResearchBridge, ask: Callable[[str], str] = input, say: Callable[[str], None] = print,
                add: int = 100) -> bool:
    say("Finding your Copper in the game's memory.")
    say("Open your bag in the game and look at the money. It shows silver: 8.00 means 800 copper.")
    amount = None
    while amount is None:
        amount = parse_money(ask("How much money do you have right now (as shown, e.g. 8.00)? "))
    report = bridge.scan_u64(amount)
    say(f"{report.count} places in memory hold {amount} ({report.ms} ms).")
    rounds = 0
    while report.count > 3:
        rounds += 1
        say("Now change your money in the game: buy or sell something cheap, or pick up coins.")
        text = ask("New amount (as shown), or q to stop: ")
        if text.strip().lower() == "q":
            return False
        new = parse_money(text)
        if new is None:
            continue
        if new == amount:
            say("That's the same amount; change it first.")
            continue
        amount = new
        report = bridge.rescan_u64(amount)
        say(f"{report.count} places still match.")
    if report.count == 0:
        say("Nothing matches any more: the game keeps money in a form this search doesn't see. Send me "
            "crimsonipelago_plugin.log from the bin64 folder.")
        return False

    say(f"Found {report.count} candidate(s) after {rounds} change(s):")
    ranked = []
    for addr in report.hits:
        start = max(0, addr - CONTEXT)
        data = bridge.read(start, CONTEXT * 2)
        rel = nearby_key(data, addr - start)
        ranked.append((rel is None, abs(rel) if rel is not None else 0, addr, rel, data))
        say(f"  0x{addr:x}: item key 1 {'at ' + format(rel, '+d') + ' bytes' if rel is not None else 'not nearby'}")
        say(f"    {data[CONTEXT - 32:CONTEXT].hex()} [{data[CONTEXT:CONTEXT + 8].hex()}] {data[CONTEXT + 8:CONTEXT + 40].hex()}")
    ranked.sort()
    target = ranked[0][2]
    answer = ask(f"Add {add} copper to 0x{target:x} now, while you play? (y/n) ").strip().lower()
    if answer != "y":
        return False
    written, found = bridge.write_u64(target, amount, amount + add)
    if not written:
        say(f"Not written: the value there is {found} now, not {amount}. Change nothing in the game and try again.")
        return False
    say(f"Written: {amount} -> {amount + add}. Check your money in the game (close and reopen the bag).")
    say("Tell me what the game shows, and whether it is still there after a save and a reload.")
    return True
