"""Structural parser for the Crimson Desert reflective serializer blob.

This reads only the self-describing parts of the blob: the type schema and the
table of contents (TOC). That is enough to fingerprint every top-level save
object (QuestSaveData, KnowledgeSaveData, InventorySaveData, ...) and tell
which ones changed between two saves, without understanding their fields.

Field-level decoding is delegated to an optional backend (see
nattkh_backend.py); this module is the always-available, dependency-free floor.

Layout (little-endian, as documented by the NattKh save editor project):
    0x00  4   magic FF FF 04 00
    0x0E  u16 header tag, u16 zero, u16 type count
          u32 len + ascii root type name
          per type: u16 field count; per field: (u32 len + name)(u32 len + type name)
                    u16 meta kind, u16 meta size, u32 meta aux;
                    then (except after the last type) u32 len + next type name
    schema_end: u32 zero, u32 toc count, u32 stream size
          toc entries: 5 x u32 (class index, sentinel1, sentinel2, data offset, data size)
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field

from .save_container import RAW_BLOB_MAGIC

SCHEMA_OFFSET = 0x0E
TOC_ENTRY_SIZE = 20
MAX_NAME_LENGTH = 512


class ParcFormatError(ValueError):
    pass


@dataclass(frozen=True)
class FieldDef:
    name: str
    type_name: str
    meta_kind: int
    meta_size: int
    meta_aux: int


@dataclass(frozen=True)
class TypeDef:
    index: int
    name: str
    fields: tuple[FieldDef, ...]


@dataclass(frozen=True)
class TocEntry:
    index: int
    class_index: int
    class_name: str
    data_offset: int
    data_size: int


@dataclass
class ParcStructure:
    root_type: str
    types: list[TypeDef]
    schema_end: int
    stream_size: int
    toc: list[TocEntry]
    schema_sha256: str
    warnings: list[str] = field(default_factory=list)

    def type_names(self) -> list[str]:
        return [t.name for t in self.types]


class _Reader:
    def __init__(self, raw: bytes, pos: int):
        self.raw, self.pos = raw, pos

    def need(self, n: int) -> None:
        if self.pos + n > len(self.raw):
            raise ParcFormatError(f"unexpected end of blob at 0x{self.pos:X} (need {n} bytes)")

    def u16(self) -> int:
        self.need(2)
        v = struct.unpack_from("<H", self.raw, self.pos)[0]
        self.pos += 2
        return v

    def u32(self) -> int:
        self.need(4)
        v = struct.unpack_from("<I", self.raw, self.pos)[0]
        self.pos += 4
        return v

    def name(self) -> str:
        length = self.u32()
        if length > MAX_NAME_LENGTH:
            raise ParcFormatError(f"implausible name length {length} at 0x{self.pos - 4:X}")
        self.need(length)
        value = self.raw[self.pos:self.pos + length].decode("ascii", errors="replace")
        self.pos += length
        return value


def parse_structure(raw: bytes) -> ParcStructure:
    if not raw.startswith(RAW_BLOB_MAGIC):
        raise ParcFormatError("blob does not start with the serializer magic FF FF 04 00")
    r = _Reader(raw, SCHEMA_OFFSET)
    r.u16()  # header tag
    r.u16()  # always zero in observed saves
    type_count = r.u16()
    root = r.name()

    types: list[TypeDef] = []
    current = root
    for index in range(type_count):
        field_count = r.u16()
        fields = []
        for _ in range(field_count):
            fname = r.name()
            tname = r.name()
            kind, size = r.u16(), r.u16()
            aux = r.u32()
            fields.append(FieldDef(fname, tname, kind, size, aux))
        types.append(TypeDef(index, current, tuple(fields)))
        if index != type_count - 1:
            current = r.name()
    schema_end = r.pos

    warnings: list[str] = []
    r.u32()  # zero prefix
    toc_count = r.u32()
    stream_size = r.u32()
    if toc_count * TOC_ENTRY_SIZE > len(raw) - r.pos:
        raise ParcFormatError(f"TOC claims {toc_count} entries, more than the blob can hold")
    toc = []
    for i in range(toc_count):
        class_index, _s1, _s2, data_offset, data_size = struct.unpack_from("<5I", raw, r.pos)
        r.pos += TOC_ENTRY_SIZE
        if class_index >= len(types):
            warnings.append(f"toc[{i}] has class index {class_index} outside the schema")
            class_name = f"<class_{class_index}>"
        else:
            class_name = types[class_index].name
        if data_offset + data_size > len(raw):
            warnings.append(f"toc[{i}] ({class_name}) points outside the blob")
        toc.append(TocEntry(i, class_index, class_name, data_offset, data_size))

    return ParcStructure(
        root_type=root,
        types=types,
        schema_end=schema_end,
        stream_size=stream_size,
        toc=toc,
        schema_sha256=hashlib.sha256(raw[SCHEMA_OFFSET:schema_end]).hexdigest(),
        warnings=warnings,
    )


def object_digests(raw: bytes, structure: ParcStructure) -> list[dict]:
    """sha256 of each top-level object's bytes, in TOC order."""
    out = []
    for entry in structure.toc:
        chunk = raw[entry.data_offset:entry.data_offset + entry.data_size]
        out.append({
            "index": entry.index,
            "class": entry.class_name,
            "offset": entry.data_offset,
            "size": entry.data_size,
            "sha256": hashlib.sha256(chunk).hexdigest(),
        })
    return out


# ------------------------------------------------------------------ test helper
def build_blob(types: list[tuple[str, list[tuple[str, str, int, int, int]]]], objects: list[tuple[int, bytes]]) -> bytes:
    """Construct a syntactically valid blob. Used by tests and the simulated game.

    types:   [(type_name, [(field_name, field_type, kind, size, aux), ...]), ...]
    objects: [(class_index, payload_bytes), ...]
    """
    def name(s: str) -> bytes:
        b = s.encode("ascii")
        return struct.pack("<I", len(b)) + b

    out = bytearray(RAW_BLOB_MAGIC + b"\x00" * (SCHEMA_OFFSET - len(RAW_BLOB_MAGIC)))
    out += struct.pack("<HHH", 0x0001, 0, len(types))
    for i, (tname, fields) in enumerate(types):
        if i == 0:
            out += name(tname)
        out += struct.pack("<H", len(fields))
        for fname, ftype, kind, size, aux in fields:
            out += name(fname) + name(ftype) + struct.pack("<HHI", kind, size, aux)
        if i != len(types) - 1:
            out += name(types[i + 1][0])
    toc_start = len(out) + 12
    data_start = toc_start + TOC_ENTRY_SIZE * len(objects)
    payload = bytearray()
    entries = bytearray()
    for class_index, data in objects:
        entries += struct.pack("<5I", class_index, 0xFFFFFFFF, 0xFFFFFFFF, data_start + len(payload), len(data))
        payload += data
    out += struct.pack("<III", 0, len(objects), len(payload))
    out += entries + payload
    return bytes(out)
