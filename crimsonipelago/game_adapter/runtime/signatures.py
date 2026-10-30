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
    kind: str                       # "function" | "global" | "via_call"
    pattern: tuple[int | None, ...]  # None = any byte
    disp_at: int = 0                # global: where the displacement is, from the match
    insn_end: int = 0               # global: where the instruction reading it ends
    call_at: int = 0                # via_call: where the call (e8 rel32) is, from the match
    then: tuple[int | None, ...] = ()   # via_call: the instruction reading the global, in the called function
    then_disp_at: int = 0
    then_insn_end: int = 0

    @property
    def regex(self) -> re.Pattern:
        return re.compile(b"".join(b"." if b is None else re.escape(bytes([b])) for b in self.pattern), re.S)


def parse_pattern(text: str) -> tuple[int | None, ...]:
    return tuple(None if t == "??" else int(t, 16) for t in text.split())


def load_signatures(path: Path = TARGETS_FILE) -> tuple[dict[str, Signature], dict]:
    """The signatures in targets.json, and the values that go with them (r3 settings that don't move)."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    sigs = {name: Signature(name, s["kind"], parse_pattern(s["pattern"]), int(s.get("disp_at", 0)),
                            int(s.get("insn_end", 0)), int(s.get("call_at", 0)), parse_pattern(s.get("then", "")),
                            int(s.get("then_disp_at", 0)), int(s.get("then_insn_end", 0)))
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
        if not hits:
            found.problems[name] = "not found"
        elif sig.kind == "function":
            if len(hits) == 1:
                found.targets[name] = hits[0]
            else:
                found.problems[name] = f"found {len(hits)} times"
        else:
            # a global: several matches are fine as long as they all lead to the same one
            values = {v for v in (_resolve(read, base, sig, at) for at in hits) if v is not None}
            if len(values) == 1:
                found.targets[name] = values.pop()
            else:
                found.problems[name] = (f"found {len(hits)} times, leading to {len(values)} different places"
                                        if values else f"found {len(hits)} times, but couldn't be followed")
    found.seconds = round(clock() - start, 1)
    return found


def _rel32(read, addr: int) -> int | None:
    raw = read(addr, 4)
    return None if raw is None else struct.unpack("<i", raw)[0]


def _resolve(read: Callable[[int, int], bytes | None], base: int, sig: Signature, at: int) -> int | None:
    """The global a match of `sig` at rva `at` leads to."""
    if sig.kind == "global":
        disp = _rel32(read, base + at + sig.disp_at)
        return None if disp is None else at + sig.insn_end + disp
    if sig.kind == "via_call":
        head = read(base + at + sig.call_at, 1)
        rel = _rel32(read, base + at + sig.call_at + 1)
        if head != b"\xe8" or rel is None:
            return None
        callee = at + sig.call_at + 5 + rel
        body = read(base + callee, 96)
        if body is None:
            return None
        m = Signature("then", "global", sig.then).regex.search(body)
        if m is None:
            return None
        disp = _rel32(read, base + callee + m.start() + sig.then_disp_at)
        return None if disp is None else callee + m.start() + sig.then_insn_end + disp
    return None


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


# --------------------------------------------------------------------------- after a game update: where did they go?
ANCHOR = 8          # bytes per anchor: short enough to survive small edits, long enough to be rare
ANCHOR_HITS = 64    # an anchor found more often than this says nothing


@dataclass
class Candidate:
    rva: int                 # where the pattern would start
    matched: int             # anchors of the pattern found in their place (give or take a small shift)
    total: int
    shift: int               # how far the later anchors moved from the first (bytes added or removed)

    @property
    def score(self) -> float:
        return self.matched / max(self.total, 1)


def anchors(pattern: tuple[int | None, ...], size: int = ANCHOR) -> list[tuple[int, bytes]]:
    """Every run of `size` known bytes in a pattern, with its offset."""
    out = []
    for at in range(0, len(pattern) - size + 1):
        window = pattern[at:at + size]
        if None not in window:
            out.append((at, bytes(window)))
    return out


def read_code(read: Callable[[int, int], bytes | None], base: int,
              progress: Callable[[int, int], None] = lambda done, total: None) -> list[tuple[int, bytes]]:
    """All executable sections, read once: (rva, bytes)."""
    sections = code_sections(read, base)
    total, done, out = sum(size for _, size in sections), 0, []
    for rva, size in sections:
        buf = bytearray()
        while len(buf) < size:
            n = min(READ, size - len(buf))
            data = read(base + rva + len(buf), n)
            buf += data if data is not None else b"\0" * n
            done += n
            if done % BATCH < READ:
                progress(done, total)
        out.append((rva, bytes(buf)))
    progress(total, total)
    return out


def near_matches(code: list[tuple[int, bytes]], pattern: tuple[int | None, ...], top: int = 5,
                 window: int = 48) -> list[Candidate]:
    """Where a pattern that no longer matches exactly most likely went: each of its anchors votes for where the
    pattern would start; votes within `window` bytes of each other count together (an edit shifts what follows)."""
    marks = anchors(pattern)
    votes: dict[int, set[int]] = {}
    for offset, needle in marks:
        hits = []
        for rva, buf in code:
            i = buf.find(needle)
            while i != -1 and len(hits) <= ANCHOR_HITS:
                hits.append(rva + i - offset)
                i = buf.find(needle, i + 1)
        if len(hits) > ANCHOR_HITS:
            continue
        for start in hits:
            votes.setdefault(start, set()).add(offset)
    clusters: list[Candidate] = []
    for start in sorted(votes):
        if clusters and start - clusters[-1].rva <= window:
            c = clusters[-1]
            c.matched = len(set().union(*(votes[s] for s in votes if c.rva <= s <= start)))
            c.shift = start - c.rva
        else:
            clusters.append(Candidate(start, len(votes[start]), len(marks), 0))
    return sorted(clusters, key=lambda c: -c.matched)[:top]


def find_moved(bridge: ResearchBridge, report_dir: Path, say: Callable[[str], None] = print,
               targets_file: Path = TARGETS_FILE) -> Path | None:
    """Research, after a game update the signatures don't fit: the likeliest new place of each function, with the
    bytes there, for making new signatures (tools/make_signatures.py)."""
    info = bridge.info
    if info is None:
        raise BridgeError("not connected")
    sigs, _ = load_signatures(targets_file)
    base = int(info.module_base, 16)
    read = reader(bridge)
    say("Reading the game's code (about a minute; keep the game running)...")
    last = [0.0]

    def progress(done: int, total: int) -> None:
        if done - last[0] >= total / 10 or done == total:
            last[0] = done
            say(f"   {100 * done // max(total, 1)}%")

    code = read_code(read, base, progress)
    rows = []
    for name, sig in sigs.items():
        patterns = [(name, sig.pattern)] + ([(f"{name}.then", sig.then)] if sig.kind == "via_call" else [])
        for label, pattern in patterns:
            rx = Signature(label, "function", pattern).regex
            found = [rva + m.start() for rva, buf in code for m in rx.finditer(buf)]
            cands = near_matches(code, pattern)
            row = {"name": label, "kind": sig.kind, "exact": [hex(m) for m in found[:8]],
                   "candidates": [{"rva": hex(c.rva), "score": round(c.score, 2), "matched": c.matched,
                                   "of": c.total, "shift": c.shift,
                                   "bytes_from": hex(max(c.rva - 32, 0)),
                                   "bytes": (_slice(code, max(c.rva - 32, 0), 320) or b"").hex()}
                                  for c in cands]}
            rows.append(row)
            best = cands[0] if cands else None
            say(f"   {label:<20} " + (f"exact at {found[0]:#x}" if found else
                                       f"best guess {best.rva:#x} ({best.matched}/{best.total} pieces)" if best
                                       else "nothing close"))
    report = {"plugin": info.plugin_version,
              "game": {"exe_version": info.exe_version, "pe_timestamp": info.pe_timestamp},
              "code_bytes": sum(len(b) for _, b in code), "targets": rows}
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"r8-moved-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    say(f"Saved: {path}")
    say("Send me that file.")
    return path


def _slice(code: list[tuple[int, bytes]], rva: int, size: int) -> bytes | None:
    for start, buf in code:
        if start <= rva < start + len(buf):
            return buf[rva - start:rva - start + size]
    return None
