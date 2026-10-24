"""Find the game functions live delivery uses in a game version Crimsonipelago hasn't seen (phase 1b).

targets.json records where each function is for the game builds researched so far, but a game update moves
them. So targets.json also has a signature for each: the function's first bytes, with every byte that a rebuild
moves blanked out (made by tools/make_signatures.py from the code dumps). Given a build it doesn't know, the client
reads the game's code through the plugin, finds each signature (it must match exactly once), and keeps what it
found in its data folder, so this happens once per game version.

A global (the item table pointer) is found through an instruction that reads it: its address is that
instruction's end plus the displacement in it, read from the running game.

What signatures can't see: the layout of the game's structures (the transaction's flag at +0x1AB, the item
table's columns). The live checks that follow (the transaction's first field is its owner, the item table's
columns agree) catch a change there, and live delivery stays off instead of guessing.
"""

from __future__ import annotations

import json
import re
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .bridge import BridgeError, ResearchBridge
from .research import TARGETS_FILE

READ = 4096               # the plugin's largest read
BATCH = 256 * READ        # bytes searched at a time


@dataclass(frozen=True)
class Signature:
    name: str
    kind: str                       # "function" | "global"
    pattern: tuple[int | None, ...]  # None = any byte
    disp_at: int = 0                # global: where the displacement is, from the match
    insn_end: int = 0               # global: where the instruction reading it ends

    @property
    def regex(self) -> re.Pattern:
        return re.compile(b"".join(b"." if b is None else re.escape(bytes([b])) for b in self.pattern), re.S)


def parse_pattern(text: str) -> tuple[int | None, ...]:
    return tuple(None if t == "??" else int(t, 16) for t in text.split())


def load_signatures(path: Path = TARGETS_FILE) -> tuple[dict[str, Signature], dict]:
    """The signatures in targets.json, and the values that go with them (r3 settings that don't move)."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    sigs = {name: Signature(name, s["kind"], parse_pattern(s["pattern"]), int(s.get("disp_at", 0)),
                            int(s.get("insn_end", 0)))
            for name, s in doc.get("signatures", {}).items() if not name.startswith("_")}
    return sigs, dict(doc.get("signature_defaults", {}))


def code_sections(read: Callable[[int, int], bytes | None], base: int) -> list[tuple[int, int]]:
    """(rva, size) of each executable section, from the PE header in memory."""
    dos = read(base, 0x40) or b""
    if dos[:2] != b"MZ":
        raise ValueError("no MZ header at the module base")
    pe = struct.unpack_from("<I", dos, 0x3C)[0]
    head = read(base + pe, 24) or b""
    if head[:4] != b"PE\0\0":
        raise ValueError("no PE header")
    count, opt_size = struct.unpack_from("<H", head, 6)[0], struct.unpack_from("<H", head, 20)[0]
    table = read(base + pe + 24 + opt_size, 40 * count)
    if table is None or len(table) < 40 * count:
        raise ValueError("the section table couldn't be read")
    out = []
    for n in range(count):
        vsize, rva = struct.unpack_from("<II", table, 40 * n + 8)
        flags = struct.unpack_from("<I", table, 40 * n + 36)[0]
        if flags & 0x20000000 and vsize:              # IMAGE_SCN_MEM_EXECUTE
            out.append((rva, vsize))
    return out


@dataclass
class Found:
    targets: dict[str, int] = field(default_factory=dict)   # name -> rva
    matches: dict[str, list[int]] = field(default_factory=dict)
    problems: dict[str, str] = field(default_factory=dict)
    scanned: int = 0
    seconds: float = 0.0

    @property
    def complete(self) -> bool:
        return not self.problems


def reader(bridge: ResearchBridge) -> Callable[[int, int], bytes | None]:
    """bridge.read, with None for memory the plugin can't read (the connection going is still an error)."""
    def read(addr: int, size: int) -> bytes | None:
        try:
            return bridge.read(addr, size)
        except BridgeError:
            if not bridge.connected:
                raise
            return None
    return read


def scan(read: Callable[[int, int], bytes | None], base: int, sigs: dict[str, Signature],
         progress: Callable[[int, int], None] = lambda done, total: None,
         clock: Callable[[], float] = time.monotonic) -> Found:
    """Every place each signature matches in the game's code, then the targets where exactly one does."""
    start = clock()
    found = Found(matches={name: [] for name in sigs})
    regexes = {name: s.regex for name, s in sigs.items()}
    overlap = max((len(s.pattern) for s in sigs.values()), default=1) - 1
    sections = code_sections(read, base)
    total = sum(size for _, size in sections)
    for rva, size in sections:
        tail, tail_at = b"", rva
        offset = 0
        while offset < size:
            chunk = bytearray()
            while offset < size and len(chunk) < BATCH:
                n = min(READ, size - offset)
                data = read(base + rva + offset, n)
                chunk += data if data is not None else b"\0" * n     # an unreadable page: nothing to find there
                offset += n
            buf = tail + bytes(chunk)
            for name, rx in regexes.items():
                for m in rx.finditer(buf):
                    at = tail_at + m.start()
                    if at not in found.matches[name]:
                        found.matches[name].append(at)
            tail = buf[-overlap:] if overlap else b""
            tail_at = rva + offset - len(tail)
            found.scanned += len(chunk)
            progress(found.scanned, total)
    for name, sig in sigs.items():
        hits = found.matches[name]
        if len(hits) != 1:
            found.problems[name] = "not found" if not hits else f"found {len(hits)} times"
            continue
        if sig.kind == "function":
            found.targets[name] = hits[0]
        else:
            raw = read(base + hits[0] + sig.disp_at, 4)
            if raw is None:
                found.problems[name] = "found, but its displacement couldn't be read"
                continue
            found.targets[name] = hits[0] + sig.insn_end + struct.unpack("<i", raw)[0]
    found.seconds = round(clock() - start, 1)
    return found


def r3_from(found: Found, defaults: dict) -> dict:
    """targets.json's r3 section for a build, from what the signatures found."""
    r3 = {k: v for k, v in defaults.items() if not k.startswith("_")}
    r3.update({name: hex(rva) for name, rva in found.targets.items()})
    r3["found_by"] = "signatures"
    return r3


class FoundTargets:
    """The client's record of builds found by signature (data folder: found-targets.json)."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def get(self, pe_timestamp: int) -> dict | None:
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        entry = doc.get(str(pe_timestamp))
        return entry.get("r3") if isinstance(entry, dict) else None

    def put(self, pe_timestamp: int, r3: dict, game: str, found: Found) -> None:
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            doc = {}
        doc[str(pe_timestamp)] = {"game": game, "r3": r3, "at": datetime.now().isoformat(timespec="seconds"),
                                  "seconds": found.seconds}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(doc, indent=1), encoding="utf-8")


def find_functions(bridge: ResearchBridge, report_dir: Path, say: Callable[[str], None] = print,
                   targets_file: Path = TARGETS_FILE) -> Path | None:
    """Research: run the signature search on this game, and compare with what targets.json knows for it."""
    from .research import load_targets

    info = bridge.info
    if info is None:
        raise BridgeError("not connected")
    sigs, defaults = load_signatures(targets_file)
    base = int(info.module_base, 16)
    say(f"Searching the game's code for {len(sigs)} functions (about a minute; keep the game running)...")
    last = [0.0]

    def progress(done: int, total: int) -> None:
        if done - last[0] >= total / 10 or done == total:
            last[0] = done
            say(f"   {100 * done // max(total, 1)}%")

    found = scan(reader(bridge), base, sigs, progress)
    build = load_targets(info.pe_timestamp, targets_file)
    known = {k: int(v, 16) for k, v in ((build or {}).get("r3") or {}).items()
             if k in sigs and isinstance(v, str) and v.startswith("0x")}
    rows = []
    for name in sigs:
        got = found.targets.get(name)
        expect = known.get(name)
        verdict = (found.problems.get(name) or "found") + (
            "" if expect is None or got is None else (", where expected" if got == expect else f", but expected {expect:#x}"))
        rows.append({"name": name, "found": hex(got) if got is not None else None,
                     "expected": hex(expect) if expect is not None else None,
                     "matches": [hex(m) for m in found.matches[name][:8]], "verdict": verdict})
        say(f"   {name:<16} {verdict}" + (f" at {got:#x}" if got is not None else ""))
    agree = found.complete and all(found.targets[k] == v for k, v in known.items())
    report = {"plugin": info.plugin_version, "game": {"exe_version": info.exe_version, "pe_timestamp": info.pe_timestamp},
              "scanned": found.scanned, "seconds": found.seconds, "targets": rows,
              "outcome": "all found where expected" if agree and known else
                         ("all found" if found.complete else "not all found")}
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"r7-signatures-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    say(f"{report['outcome'].capitalize()} ({found.scanned / 1e6:.0f} MB of code in {found.seconds:.0f} s).")
    say(f"Saved: {path}")
    say("Send me that file.")
    return path
