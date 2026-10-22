"""PAPGT / PAMT / PAZ archives: list and extract game files, and build overlay groups.

Layout (little-endian), as documented by crimson-rs:

    <game>/meta/0.papgt   group registry: which NNNN/ groups load, first match wins
    <game>/NNNN/0.pamt    a group's manifest: chunks, directory/file name tries, file table
    <game>/NNNN/K.paz     headerless chunk files holding the (compressed, maybe encrypted) data

PAPGT: u32 unknown, u32 checksum(post-header), u8 entry_count, u8, u16
       entries[entry_count]: u8 is_optional, u16 language, u8 0, u32 name_offset, u32 pamt_checksum
       i32 names_len + names (NUL-terminated strings)
PAMT:  u32 checksum(post-header), u16 chunk_count, u16 0, u8 unknown, u8[3] encrypt_info
       chunks[chunk_count]: u32 id, u32 checksum, u32 size
       u32 len + directory-name trie; u32 len + file-name trie
       u32 n + dirs[n]: u32 name_checksum, i32 name_offset, u32 first_file, u32 file_count
       u32 n + files[n]: u32 name_offset, u32 chunk_offset, u32 stored_size, u32 size,
                         u16 chunk_id, u8 flags (low 4 bits compression, high 4 bits crypto), u8 0
Trie entry: i32 parent_offset (-1 = root), u8 length, bytes; a name is its parents' text + its own.
"""

from __future__ import annotations

import fnmatch
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import lz4.block
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms

from .checksum import checksum

COMPRESSION = {0: "none", 1: "partial", 2: "lz4", 3: "zlib", 4: "quicklz"}
CRYPTO = {0: "none", 1: "ice", 2: "aes", 3: "chacha20"}
_PACK_BASE_KEY = bytes([0x0E, 0x0F, 0x0C, 0x0D, 0x04, 0x05, 0x06, 0x07, 0x02, 0x03, 0x00, 0x01, 0x08, 0x09, 0x0A, 0x0B,
                        0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F])


class ArchiveError(ValueError):
    pass


class _Reader:
    def __init__(self, data: bytes, what: str):
        self.data, self.pos, self.what = data, 0, what

    def take(self, fmt: str):
        size = struct.calcsize(fmt)
        if self.pos + size > len(self.data):
            raise ArchiveError(f"{self.what}: truncated at offset {self.pos}")
        values = struct.unpack_from(fmt, self.data, self.pos)
        self.pos += size
        return values

    def blob(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise ArchiveError(f"{self.what}: truncated at offset {self.pos}")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out


def trie_string(buf: bytes, offset: int, cache: dict[int, str] | None = None) -> str:
    parts, seen = [], set()
    while offset != -1:
        if cache is not None and offset in cache:
            parts.append(cache[offset])
            break
        if offset in seen or offset < 0 or offset + 5 > len(buf):
            raise ArchiveError(f"bad name-table offset {offset}")
        seen.add(offset)
        parent, length = struct.unpack_from("<iB", buf, offset)
        if offset + 5 + length > len(buf):
            raise ArchiveError(f"name-table entry at {offset} runs past the buffer")
        parts.append(buf[offset + 5:offset + 5 + length].decode("utf-8"))
        offset = parent
    return "".join(reversed(parts))


# --------------------------------------------------------------------------- PAPGT
@dataclass(frozen=True)
class GroupEntry:
    name: str
    is_optional: int
    language: int
    pamt_checksum: int


@dataclass
class Papgt:
    """The group registry. Keeps every header value and the raw name buffer so edits are minimal and
    reversible: add_front + remove of the same group restores the original bytes exactly."""

    unknown0: int
    checksum: int
    unknown1: int
    unknown2: int
    raw_entries: list[tuple[int, int, int, int, int]]   # optional, language, zero, name_offset, pamt_checksum
    names: bytes

    @classmethod
    def parse(cls, data: bytes, *, verify: bool = True) -> Papgt:
        r = _Reader(data, "0.papgt")
        unknown0, stored, count, unknown1, unknown2 = r.take("<IIBBH")
        if verify and checksum(data[12:]) != stored:
            raise ArchiveError("0.papgt checksum mismatch: damaged file or unsupported format")
        raw = [r.take("<BHBII") for _ in range(count)]
        (names_len,) = r.take("<i")
        names = r.blob(names_len)
        papgt = cls(unknown0, stored, unknown1, unknown2, raw, names)
        _ = papgt.entries  # validates every name offset
        return papgt

    @property
    def entries(self) -> list[GroupEntry]:
        out = []
        for optional, language, _zero, name_off, pamt_sum in self.raw_entries:
            end = self.names.find(b"\0", name_off)
            if name_off >= len(self.names) or end < 0:
                raise ArchiveError(f"0.papgt: bad group name offset {name_off}")
            out.append(GroupEntry(self.names[name_off:end].decode("ascii"), optional, language, pamt_sum))
        return out

    def add_front(self, name: str, pamt_checksum: int, *, is_optional: int = 0, language: int = 0x3FFF) -> None:
        """Register a group with top priority (what the community mod tools do)."""
        if any(e.name == name for e in self.entries):
            raise ArchiveError(f"group {name} is already registered")
        if len(self.raw_entries) >= 255:
            raise ArchiveError("the registry is full (255 groups)")
        prefix = name.encode("ascii") + b"\0"
        self.raw_entries = [(is_optional, language, 0, 0, pamt_checksum)] + [
            (o, lang, z, off + len(prefix), c) for o, lang, z, off, c in self.raw_entries]
        self.names = prefix + self.names

    def remove(self, name: str) -> None:
        """Unregister a group; if its name sits at the front of the buffer (as add_front leaves it) and
        nothing else uses those bytes, they are removed too, restoring the original layout."""
        index = next((i for i, e in enumerate(self.entries) if e.name == name), None)
        if index is None:
            raise ArchiveError(f"group {name} is not registered")
        _o, _l, _z, off, _c = self.raw_entries.pop(index)
        prefix = name.encode("ascii") + b"\0"
        if off == 0 and self.names.startswith(prefix) and all(e[3] >= len(prefix) for e in self.raw_entries):
            self.names = self.names[len(prefix):]
            self.raw_entries = [(o, lang, z, n - len(prefix), c) for o, lang, z, n, c in self.raw_entries]

    def to_bytes(self) -> bytes:
        body = b"".join(struct.pack("<BHBII", *e) for e in self.raw_entries)
        body += struct.pack("<i", len(self.names)) + self.names
        return struct.pack("<IIBBH", self.unknown0, checksum(body), len(self.raw_entries),
                           self.unknown1, self.unknown2) + body


# --------------------------------------------------------------------------- PAMT
@dataclass(frozen=True)
class VirtualFile:
    group: str
    path: str            # directory + "/" + name, e.g. gamedata/binary__/client/bin/skill.pabgb
    chunk_id: int
    chunk_offset: int
    stored_size: int
    size: int
    flags: int

    @property
    def compression(self) -> str:
        return COMPRESSION.get(self.flags & 0x0F, f"unknown({self.flags & 0x0F})")

    @property
    def crypto(self) -> str:
        return CRYPTO.get(self.flags >> 4, f"unknown({self.flags >> 4})")

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


@dataclass
class Pamt:
    checksum: int
    unknown: int
    encrypt_info: bytes
    chunks: dict[int, tuple[int, int]]          # id -> (checksum, size)
    files: list[VirtualFile] = field(default_factory=list)

    @classmethod
    def parse(cls, data: bytes, group: str = "", *, verify: bool = True, expected: int | None = None) -> Pamt:
        r = _Reader(data, f"{group}/0.pamt")
        stored, count, zero, unknown = r.take("<IHHB")
        encrypt_info = r.blob(3)
        if verify:
            actual = checksum(data[12:])
            if actual != stored or (expected is not None and actual != expected):
                raise ArchiveError(f"{group}/0.pamt checksum mismatch: damaged file or unsupported format")
        if zero != 0:
            raise ArchiveError(f"{group}/0.pamt: unexpected header value {zero}")
        chunks = {}
        for _ in range(count):
            cid, csum, size = r.take("<III")
            chunks[cid] = (csum, size)
        dir_names = r.blob(r.take("<I")[0])
        file_names = r.blob(r.take("<I")[0])
        dirs = [r.take("<IiII") for _ in range(r.take("<I")[0])]
        raw_files = [r.take("<IIIIHBB") for _ in range(r.take("<I")[0])]

        pamt = cls(stored, unknown, encrypt_info, chunks)
        dcache: dict[int, str] = {}
        fcache: dict[int, str] = {}
        for name_sum, name_off, first, n in dirs:
            dir_path = trie_string(dir_names, name_off, dcache)
            if verify and checksum(dir_path.encode("utf-8")) != name_sum:
                raise ArchiveError(f"{group}/0.pamt: directory name checksum mismatch for {dir_path!r}")
            if first + n > len(raw_files):
                raise ArchiveError(f"{group}/0.pamt: directory {dir_path!r} points past the file table")
            for name_off_f, c_off, stored_size, size, cid, flags, _z in raw_files[first:first + n]:
                if cid not in chunks:
                    raise ArchiveError(f"{group}/0.pamt: file references unknown chunk {cid}")
                if c_off + stored_size > chunks[cid][1]:
                    raise ArchiveError(f"{group}/0.pamt: file data runs past chunk {cid}")
                name = trie_string(file_names, name_off_f, fcache)
                path = f"{dir_path}/{name}" if dir_path else name
                pamt.files.append(VirtualFile(group, path, cid, c_off, stored_size, size, flags))
        return pamt


def _pack_cipher(data: bytes, encrypt_info: bytes, path: str) -> bytes:
    counter = checksum(path.rsplit("/", 1)[-1].encode("utf-8"))
    nonce16 = struct.pack("<I", counter) * 4
    k = encrypt_info[0] ^ encrypt_info[1] ^ encrypt_info[2]
    key = bytes(b ^ nonce16[i % 16] ^ k for i, b in enumerate(_PACK_BASE_KEY))
    return Cipher(algorithms.ChaCha20(key, nonce16), mode=None).encryptor().update(data)


def decode_entry(raw: bytes, vf: VirtualFile, encrypt_info: bytes) -> bytes:
    """Stored bytes -> original file: decrypt (ChaCha20) and decompress (LZ4 / zlib)."""
    if vf.crypto == "chacha20":
        raw = _pack_cipher(raw, encrypt_info, vf.path)
    elif vf.crypto != "none":
        raise ArchiveError(f"{vf.path}: {vf.crypto} encryption is not supported")
    if vf.compression == "none":
        out = raw
    elif vf.compression == "lz4":
        out = lz4.block.decompress(raw, uncompressed_size=vf.size)
    elif vf.compression == "zlib":
        out = zlib.decompress(raw)
    else:
        raise ArchiveError(f"{vf.path}: {vf.compression} compression is not supported")
    if len(out) != vf.size:
        raise ArchiveError(f"{vf.path}: unpacked {len(out)} bytes, the index says {vf.size}")
    return out


# --------------------------------------------------------------------------- game folder
class GameArchive:
    """All groups of an installed game, in load order (first match wins, like the game)."""

    def __init__(self, game_dir: str | Path, *, verify: bool = True):
        self.root = Path(game_dir)
        papgt_path = self.root / "meta" / "0.papgt"
        if not papgt_path.is_file():
            raise ArchiveError(f"{self.root} is not a Crimson Desert folder (no meta/0.papgt)")
        self.verify = verify
        self.papgt = Papgt.parse(papgt_path.read_bytes(), verify=verify)
        self._pamts: dict[str, Pamt] = {}

    @property
    def groups(self) -> list[GroupEntry]:
        return self.papgt.entries

    def pamt(self, group: str) -> Pamt:
        if group not in self._pamts:
            entry = next((g for g in self.groups if g.name == group), None)
            path = self.root / group / "0.pamt"
            if not path.is_file():
                raise ArchiveError(f"group {group} is registered but {path} is missing")
            self._pamts[group] = Pamt.parse(path.read_bytes(), group, verify=self.verify,
                                            expected=entry.pamt_checksum if entry else None)
        return self._pamts[group]

    def files(self, pattern: str = "*", *, all_copies: bool = False) -> list[VirtualFile]:
        """Files whose path or name matches a glob, in load order. By default only the copy the game
        loads (the first group that has it) is returned; all_copies shows shadowed ones too."""
        out, seen = [], set()
        for g in self.groups:
            if not (self.root / g.name / "0.pamt").is_file():
                continue
            for vf in self.pamt(g.name).files:
                if not (fnmatch.fnmatch(vf.path, pattern) or fnmatch.fnmatch(vf.name, pattern)):
                    continue
                if vf.path in seen and not all_copies:
                    continue
                seen.add(vf.path)
                out.append(vf)
        return out

    def read(self, vf: VirtualFile) -> bytes:
        paz = self.root / vf.group / f"{vf.chunk_id}.paz"
        with open(paz, "rb") as fh:
            fh.seek(vf.chunk_offset)
            raw = fh.read(vf.stored_size)
        if len(raw) != vf.stored_size:
            raise ArchiveError(f"{paz} is shorter than the index says")
        return decode_entry(raw, vf, self.pamt(vf.group).encrypt_info)


# --------------------------------------------------------------------------- building groups
class _SegNode:
    def __init__(self):
        self.children: list[tuple[str, _SegNode]] = []
        self.terminals: list[int] = []


class _Radix:
    def __init__(self, fragment: str, seg_idx: int | None = None):
        self.fragment, self.seg_idx = fragment, seg_idx
        self.children: list[_Radix] = []


def _radix_insert(node: _Radix, remaining: str, seg_idx: int) -> None:
    if not remaining:
        node.seg_idx = seg_idx
        return
    for i, child in enumerate(node.children):
        common = 0
        for a, b in zip(child.fragment, remaining):
            if a != b:
                break
            common += 1
        if common == 0:
            continue
        if common == len(child.fragment):
            _radix_insert(child, remaining[common:], seg_idx)
            return
        # split the child at the shared prefix (Rust Vec::swap_remove semantics, to match the reference layout)
        old = node.children[i]
        node.children[i] = node.children[-1]
        node.children.pop()
        old.fragment = old.fragment[common:]
        split = _Radix(remaining[:common])  # the shared prefix
        split.children = [old]
        _radix_insert(split, remaining[common:], seg_idx)
        node.children.append(split)
        return
    node.children.append(_Radix(remaining, seg_idx))


def _build_trie(strings: list[str]) -> tuple[bytes, list[int]]:
    """Name table in the native layout: one level per path segment ("/"-prefixed below the root), with
    siblings sharing common prefixes (radix-compressed). Same algorithm as crimson-rs build_trie_buffer."""
    root = _SegNode()
    for idx, s in enumerate(strings):
        node = root
        for seg in (s.split("/") if s else []):
            nxt = next((c for name, c in node.children if name == seg), None)
            if nxt is None:
                nxt = _SegNode()
                node.children.append((seg, nxt))
            node = nxt
        node.terminals.append(idx)

    buf = bytearray()
    offsets = [-1] * len(strings)

    def write(parent: int, text: str) -> int:
        enc = text.encode("utf-8")
        if len(enc) > 255:
            raise ArchiveError(f"name fragment too long: {text!r}")
        off = len(buf)
        buf.extend(struct.pack("<iB", parent, len(enc)) + enc)
        return off

    def emit_children(children: list[tuple[str, _SegNode]], parent: int, at_root: bool) -> None:
        if not children:
            return
        radix = _Radix("")
        for ci, (name, _node) in enumerate(children):
            _radix_insert(radix, name if at_root else "/" + name, ci)
        emit_radix(radix, parent, children)

    def emit_radix(node: _Radix, parent: int, children: list[tuple[str, _SegNode]]) -> None:
        if not node.fragment:
            for c in node.children:
                emit_radix(c, parent, children)
            return
        me = write(parent, node.fragment)
        if node.seg_idx is not None:
            seg = children[node.seg_idx][1]
            for t in seg.terminals:
                offsets[t] = me
            emit_children(seg.children, me, False)
        for c in node.children:
            emit_radix(c, me, children)

    for t in root.terminals:
        offsets[t] = write(-1, "")
    emit_children(root.children, -1, True)
    return bytes(buf), offsets


def build_group(files: dict[str, bytes], *, compression: str = "lz4",
                encrypt_info: bytes = b"\x02\x0e\x61", unknown: int = 50) -> tuple[bytes, dict[int, bytes]]:
    """Pack {virtual path: data} into a single-chunk group. Returns (0.pamt bytes, {chunk id: .paz bytes})."""
    comp_code = {v: k for k, v in COMPRESSION.items()}[compression]
    by_dir: dict[str, list[tuple[str, bytes]]] = {}
    for path, data in files.items():
        d, _, name = path.rpartition("/")
        by_dir.setdefault(d, []).append((name, data))
    dir_order = sorted(by_dir)
    paz = bytearray()
    entries = []
    for d in dir_order:
        for name, data in by_dir[d]:
            stored = lz4.block.compress(data, store_size=False) if compression == "lz4" else (
                zlib.compress(data) if compression == "zlib" else bytes(data))
            entries.append((d, name, len(paz), len(stored), len(data)))
            paz += stored
    dir_buf, dir_offs = _build_trie(dir_order)
    file_buf, file_offs = _build_trie([e[1] for e in entries])
    body = bytearray()
    body += struct.pack("<III", 0, checksum(bytes(paz)), len(paz))
    body += struct.pack("<I", len(dir_buf)) + dir_buf
    body += struct.pack("<I", len(file_buf)) + file_buf
    body += struct.pack("<I", len(dir_order))
    index = 0
    for d, off in zip(dir_order, dir_offs):
        n = len(by_dir[d])
        body += struct.pack("<IiII", checksum(d.encode("utf-8")), off, index, n)
        index += n
    body += struct.pack("<I", len(entries))
    for (_d, _name, c_off, stored_size, size), name_off in zip(entries, file_offs):
        body += struct.pack("<IIIIHBB", name_off, c_off, stored_size, size, 0, comp_code, 0)
    header = struct.pack("<IHHB", checksum(bytes(body)), 1, 0, unknown) + encrypt_info
    return header + bytes(body), {0: bytes(paz)}


def build_papgt(groups: list[GroupEntry], *, unknown0: int = 0, unknown1: int = 0, unknown2: int = 0) -> bytes:
    """Serialise a fresh registry (order = load order; first match wins). Used by tests."""
    papgt = Papgt(unknown0, 0, unknown1, unknown2, [], b"")
    for g in reversed(groups):
        papgt.add_front(g.name, g.pamt_checksum, is_optional=g.is_optional, language=g.language)
    return papgt.to_bytes()
