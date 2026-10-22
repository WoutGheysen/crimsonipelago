"""Static-info tables: a body of records plus a key -> offset index.

The pair is named `<table>.pabgb` + `<table>.pabgh` in older builds and community mods, and
`<table>.staticinfobody` + `<table>.staticinfoheader` in the current Steam build (25477059).

Index formats seen (documented in the NattKh modding notes):
    u16 count + count x (u32 key, u32 offset)     most tables
    u16 count + count x (u16 key, u32 offset)     a few small tables
    u16 count + count x (u8 key, u32 offset)      e.g. mercenaryinfo
    u32 count + count x (u32 key, u32 offset)
Records start with: u32 key, u32 name length, name bytes (some tables add a NUL). The rest of a record
is table-specific and mostly undecoded; `references` and `dump` help reverse-engineer it by finding
32-bit values that are keys of known records (skills, knowledge, quests, ...).
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path


class TableError(ValueError):
    pass


# body suffix -> index suffix
TABLE_SUFFIXES = {".pabgb": ".pabgh", ".staticinfobody": ".staticinfoheader"}


def parse_index(header: bytes) -> list[tuple[int, int]]:
    """Return (key, offset) pairs in file order."""
    if len(header) >= 2:
        n16 = struct.unpack_from("<H", header, 0)[0]
        if 2 + n16 * 8 == len(header):
            return [struct.unpack_from("<II", header, 2 + i * 8) for i in range(n16)]
        if 2 + n16 * 6 == len(header):
            return [struct.unpack_from("<HI", header, 2 + i * 6) for i in range(n16)]
        if 2 + n16 * 5 == len(header):
            return [struct.unpack_from("<BI", header, 2 + i * 5) for i in range(n16)]
    if len(header) >= 4:
        n32 = struct.unpack_from("<I", header, 0)[0]
        if 4 + n32 * 8 == len(header):
            return [struct.unpack_from("<II", header, 4 + i * 8) for i in range(n32)]
    raise TableError(f"unrecognised index format ({len(header)} bytes)")


@dataclass(frozen=True)
class Record:
    key: int
    offset: int
    data: bytes

    @property
    def name(self) -> str:
        if len(self.data) >= 8:
            k, n = struct.unpack_from("<II", self.data, 0)
            if k == self.key and 0 < n < 512 and 8 + n <= len(self.data):
                raw = self.data[8:8 + n]
                if all(32 <= c < 127 for c in raw.rstrip(b"\0")):
                    return raw.rstrip(b"\0").decode("ascii")
        return ""


class Table:
    def __init__(self, name: str, body: bytes, header: bytes):
        self.name = name
        self.body = body
        index = sorted(parse_index(header), key=lambda kv: kv[1])
        self.records: dict[int, Record] = {}
        for i, (key, off) in enumerate(index):
            end = index[i + 1][1] if i + 1 < len(index) else len(body)
            if not 0 <= off <= end <= len(body):
                raise TableError(f"{name}: record {key} has bad bounds {off}..{end}")
            self.records[key] = Record(key, off, body[off:end])
        mismatched = sum(1 for r in self.records.values()
                         if len(r.data) >= 4 and struct.unpack_from("<I", r.data, 0)[0] != r.key)
        self.key_mismatches = mismatched

    @classmethod
    def load(cls, body_path: str | Path, header_path: str | Path | None = None) -> Table:
        body_path = Path(body_path)
        if header_path is None:
            index_suffix = TABLE_SUFFIXES.get(body_path.suffix.lower())
            if index_suffix is None:
                raise TableError(f"{body_path.name}: not a table body ({', '.join(TABLE_SUFFIXES)})")
            header_path = body_path.with_suffix(index_suffix)
        header_path = Path(header_path)
        if not header_path.is_file():
            raise TableError(f"no index next to {body_path} (expected {header_path.name})")
        return cls(body_path.stem, body_path.read_bytes(), header_path.read_bytes())

    def by_name(self, pattern: str) -> list[Record]:
        rx = re.compile(pattern, re.IGNORECASE)
        return [r for r in self.records.values() if rx.search(r.name)]

    def __len__(self) -> int:
        return len(self.records)


def load_tables(folder: str | Path, *, problems: list[str] | None = None) -> dict[str, Table]:
    """Every table body with its matching index under a folder (searched recursively), in either naming scheme.
    Tables that can't be read are skipped and described in `problems`."""
    out: dict[str, Table] = {}
    bodies = sorted(p for p in Path(folder).rglob("*") if p.suffix.lower() in TABLE_SUFFIXES)
    for body in bodies:
        if body.with_suffix(TABLE_SUFFIXES[body.suffix.lower()]).is_file() and body.stem not in out:
            try:
                out[body.stem] = Table.load(body)
            except TableError as exc:
                if problems is not None:
                    problems.append(f"{body.name}: {exc}")
    return out


def u32_positions(data: bytes, value: int) -> list[int]:
    needle = struct.pack("<I", value)
    out, pos = [], data.find(needle)
    while pos != -1:
        out.append(pos)
        pos = data.find(needle, pos + 1)
    return out


def search(tables: dict[str, Table], value: int, *, skip_own_key: bool = True) -> list[tuple[str, Record, list[int]]]:
    """Records anywhere that contain `value` as a little-endian u32 (excluding a record's own key field)."""
    hits = []
    for tname, table in tables.items():
        for rec in table.records.values():
            pos = u32_positions(rec.data, value)
            if skip_own_key and rec.key == value:
                pos = [p for p in pos if p != 0]
            if pos:
                hits.append((tname, rec, pos))
    return hits


class KeyNames:
    """Reverse lookup: which tables have a record with this key, and its name."""

    def __init__(self, tables: dict[str, Table]):
        self.by_key: dict[int, list[tuple[str, str]]] = {}
        for tname, table in tables.items():
            for rec in table.records.values():
                self.by_key.setdefault(rec.key, []).append((tname, rec.name))

    def describe(self, value: int) -> str:
        return " | ".join(f"{t}:{n or '?'}" for t, n in self.by_key.get(value, []))


def references(record: Record, names: KeyNames, *, min_value: int = 1000) -> list[tuple[int, int, str]]:
    """(offset, value, description) for every aligned-or-not u32 in the record that is a known key."""
    out = []
    data = record.data
    for off in range(0, len(data) - 3):
        v = struct.unpack_from("<I", data, off)[0]
        if v >= min_value and v in names.by_key:
            out.append((off, v, names.describe(v)))
    return out


def strings(record: Record, *, min_len: int = 3) -> list[tuple[int, str]]:
    """Length-prefixed (u32) ASCII strings inside a record."""
    data, out, i = record.data, [], 0
    while i + 4 <= len(data):
        n = struct.unpack_from("<I", data, i)[0]
        if min_len <= n < 512 and i + 4 + n <= len(data):
            raw = data[i + 4:i + 4 + n].rstrip(b"\0")
            if raw and all(32 <= c < 127 for c in raw):
                out.append((i, raw.decode("ascii")))
                i += 4 + n
                continue
        i += 1
    return out


def hexdump(data: bytes, start: int = 0, width: int = 16) -> list[str]:
    lines = []
    for off in range(0, len(data), width):
        chunk = data[off:off + width]
        text = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
        lines.append(f"{start + off:6d}  {chunk.hex(' '):<{width * 3}} {text}")
    return lines
