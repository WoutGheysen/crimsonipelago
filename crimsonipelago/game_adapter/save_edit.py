"""Structural save edits: append elements to lists in the decoded save (e.g. learn a knowledge entry).

The serializer stores absolute positions, so inserting bytes anywhere moves everything after it.
What has to be kept consistent, as measured on every save collected so far (build 25477059):

* **Payload pointers.** Every object and list element has a u32 pointing at its own payload, stored right
  before it, so a pointer at position p always holds p + 4 ("self-referencing"). The decoded tree lists
  them; the few in regions the decoder skips (ContentsMiscSaveData) are found by the same pattern behind
  an all-FF sentinel. After the insert every pointer is rewritten as new position + 4.
* **Trailing sizes.** Each object and element ends with a u32 = bytes from its payload to the trailer.
  Containers that enclose an insertion point grow by the inserted size.
* **List headers.** k bytes 0x01, one 0x00, a u32 element count, 13 zero bytes. The count goes up.
* **Table of contents.** Offsets and sizes of the top-level objects, and the stream size (= blob length).

New elements are copies of an existing element of the same list with exactly the same fields present,
with every field value given explicitly, so nothing is inherited from the copied entry by accident.

The result is decoded again and compared with the original: the edit fails unless the only differences
are the new elements and the counts.
"""

from __future__ import annotations

import struct
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any, Iterator

from . import parc

SENTINEL = b"\xff" * 8
_FMT = {1: "<B", 2: "<H", 4: "<I", 8: "<Q"}


class SaveEditError(ValueError):
    pass


@dataclass(frozen=True)
class ListAppend:
    """Append one element to `obj_class.list_name`, with these child field values (all of them)."""
    obj_class: str
    list_name: str
    values: dict[str, int]


@dataclass
class EditReport:
    log: list[str] = field(default_factory=list)
    growth: int = 0


def _walk(fields) -> Iterator[Any]:
    for f in fields or []:
        if not f.present:
            continue
        yield f
        if f.child_fields:
            yield from _walk(f.child_fields)
        for e in f.list_elements or []:
            yield e
            if e.child_fields:
                yield from _walk(e.child_fields)


def _containers(node) -> Iterator[Any]:
    """The node and every object/element inside it that has its own payload pointer."""
    if (getattr(node, "child_payload_offset", 0) or 0) > 0 and node.child_fields is not None:
        yield node
    for n in _walk(node.child_fields):
        if (getattr(n, "child_payload_offset", 0) or 0) > 0 and n.child_fields is not None:
            yield n


def _u32(raw, pos: int) -> int:
    return struct.unpack_from("<I", raw, pos)[0]


def pointer_sites(raw: bytes, objects) -> set[int]:
    """Positions of every payload pointer (value == position + 4)."""
    sites = set()
    for obj in objects:
        for node in _walk(obj.fields):
            cpo = getattr(node, "child_payload_offset", 0) or 0
            if cpo > 0 and node.child_fields is not None:
                if _u32(raw, cpo - 4) != cpo:
                    raise SaveEditError(f"unexpected payload pointer in {obj.class_name}.{node.name} at 0x{cpo - 4:X}")
                sites.add(cpo - 4)
    pos = raw.find(SENTINEL)
    while pos != -1:
        p = pos + 8
        if p + 4 <= len(raw) and _u32(raw, p) == p + 4:
            sites.add(p)
        pos = raw.find(SENTINEL, pos + 1)
    return sites


def _list_header(raw: bytes, f) -> tuple[int, int]:
    """(position of the u32 count, count) for a list field; refuses layouts not seen before."""
    start = f.start_offset
    k = 0
    while raw[start + k] == 1 and k < 8:
        k += 1
    if raw[start + k] != 0:
        raise SaveEditError(f"list {f.name}: unknown header layout {raw[start:start + 24].hex()}")
    count_pos = start + k + 1
    count = _u32(raw, count_pos)
    tail = raw[count_pos + 4:count_pos + 17]
    if tail != b"\x00" * 13 or f.list_header_size != k + 18 or count != len(f.list_elements or []):
        raise SaveEditError(f"list {f.name}: unknown header layout {raw[start:start + 24].hex()}")
    return count_pos, count


def _child_values(raw: bytes, elem) -> dict[str, int]:
    out = {}
    for cf in elem.child_fields or []:
        if cf.present:
            fmt = _FMT.get(cf.end_offset - cf.start_offset)
            if fmt:
                out[cf.name] = struct.unpack_from(fmt, raw, cf.start_offset)[0]
    return out


def _find(objects, cls: str, list_name: str):
    objs = [o for o in objects if o.class_name == cls]
    if len(objs) != 1:
        raise SaveEditError(f"expected one {cls} in the save, found {len(objs)}")
    lists = [f for f in objs[0].fields if f.present and f.name == list_name and f.list_elements is not None]
    if len(lists) != 1:
        raise SaveEditError(f"{cls}.{list_name} not found")
    return objs[0], lists[0]


def _clone(raw: bytes, template, values: dict[str, int], new_pos: int) -> bytes:
    start, end = template.start_offset, template.end_offset
    data = bytearray(raw[start:end])
    present = {cf.name: cf for cf in template.child_fields or [] if cf.present}
    for name, value in values.items():
        cf = present[name]
        fmt = _FMT.get(cf.end_offset - cf.start_offset)
        if fmt is None:
            raise SaveEditError(f"field {name} has an unsupported size")
        struct.pack_into(fmt, data, cf.start_offset - start, value)
    for node in _containers(template):
        rel = node.child_payload_offset - 4 - start
        struct.pack_into("<I", data, rel, new_pos + rel + 4)
    return bytes(data)


def append_elements(raw: bytes, result: dict, appends: list[ListAppend]) -> tuple[bytes, EditReport]:
    """Return a new blob with the elements appended. `result` is the backend's parse of `raw`."""
    objects = result["objects"]
    structure = parc.parse_structure(raw)
    report = EditReport()

    # ---- plan: where each element goes and what it contains (positions in the ORIGINAL blob)
    inserts: list[tuple[int, Any, ListAppend, Any]] = []   # (offset, template, spec, list field)
    count_bumps: dict[int, int] = {}
    for spec in appends:
        obj, lst = _find(objects, spec.obj_class, spec.list_name)
        count_pos, count = _list_header(raw, lst)
        if lst.end_offset != (lst.list_elements[-1].end_offset if lst.list_elements else lst.start_offset + lst.list_header_size):
            raise SaveEditError(f"{spec.list_name}: bytes after the last element; layout not understood")
        wanted = set(spec.values)
        candidates = [e for e in lst.list_elements
                      if e.child_fields is not None and {cf.name for cf in e.child_fields if cf.present} == wanted]
        if not candidates:
            raise SaveEditError(f"{spec.obj_class}.{spec.list_name}: no existing entry with exactly the fields "
                                f"{sorted(wanted)} to copy (the save may be too early in the game)")
        template = candidates[-1]
        for node in _containers(template):
            if _u32(raw, node.end_offset - 4) != node.end_offset - 4 - node.child_payload_offset:
                raise SaveEditError(f"{spec.list_name}: element layout not understood (trailing size)")
        inserts.append((lst.end_offset, template, spec, lst))
        count_bumps[count_pos] = count_bumps.get(count_pos, count) + 1
        report.log.append(f"{spec.obj_class}.{spec.list_name}: added {spec.values}")

    # Containers that enclose an insertion point grow; check their trailers before trusting them.
    growth_at: dict[int, int] = {}
    for offset, template, _, _ in inserts:
        growth_at[offset] = growth_at.get(offset, 0) + (template.end_offset - template.start_offset)
    enclosing: list[tuple[int, int]] = []   # (trailer position, growth)
    for obj in objects:
        if not any(obj.data_offset <= off <= obj.data_offset + obj.data_size for off in growth_at):
            continue
        for node in _walk(obj.fields):
            cpo = getattr(node, "child_payload_offset", 0) or 0
            if cpo <= 0 or node.child_fields is None:
                continue
            g = sum(n for off, n in growth_at.items() if node.start_offset < off < node.end_offset)
            if g:
                if _u32(raw, node.end_offset - 4) != node.end_offset - 4 - cpo:
                    raise SaveEditError(f"{obj.class_name}.{node.name}: trailing size not understood")
                enclosing.append((node.end_offset - 4, g))

    sites = pointer_sites(raw, objects)

    # ---- edits that keep positions: counts, trailers, table of contents
    work = bytearray(raw)
    for pos, value in count_bumps.items():
        struct.pack_into("<I", work, pos, value)
    for pos, g in enclosing:
        struct.pack_into("<I", work, pos, _u32(work, pos) + g)

    offsets = sorted(growth_at)
    cumulative = []
    total = 0
    for off in offsets:
        total += growth_at[off]
        cumulative.append(total)

    def shift(x: int) -> int:
        i = bisect_right(offsets, x)
        return x + (cumulative[i - 1] if i else 0)

    toc_base = structure.schema_end + 12
    for entry in structure.toc:
        tp = toc_base + entry.index * parc.TOC_ENTRY_SIZE
        start, end = entry.data_offset, entry.data_offset + entry.data_size
        # Bytes inserted exactly at a boundary belong to the block that ends there (lists are appended to).
        new_start, new_end = shift(start), shift(end)
        struct.pack_into("<I", work, tp + 12, new_start)
        struct.pack_into("<I", work, tp + 16, new_end - new_start)
    struct.pack_into("<I", work, structure.schema_end + 8, len(raw) + total)

    # ---- splice
    out = bytearray()
    cursor = 0
    by_offset: dict[int, list] = {}
    for offset, template, spec, _ in inserts:
        by_offset.setdefault(offset, []).append((template, spec))
    for off in offsets:
        out += work[cursor:off]
        for template, spec in by_offset[off]:
            out += _clone(raw, template, spec.values, len(out))
        cursor = off
    out += work[cursor:]
    if len(out) != len(raw) + total:
        raise SaveEditError("internal error: size mismatch after splice")

    for p in sites:
        np = shift(p)
        struct.pack_into("<I", out, np, np + 4)

    report.growth = total
    return bytes(out), report


# --------------------------------------------------------------------------- verification
def _blank(raw: bytes, sites: set[int]) -> bytes:
    out = bytearray(raw)
    for p in sites:
        out[p:p + 4] = b"\0\0\0\0"
    return bytes(out)


def verify_append(before: bytes, after: bytes, backend, appends: list[ListAppend],
                  before_result: dict | None = None) -> list[str]:
    """Differences between the two blobs other than the requested new elements."""
    problems: list[str] = []
    before_result = before_result or backend.parse(before)
    after_result = backend.parse(after)
    try:
        sites_before = pointer_sites(before, before_result["objects"])
        sites_after = pointer_sites(after, after_result["objects"])
    except SaveEditError as exc:
        return [str(exc)]
    if len(sites_after) != len(sites_before) + len(appends):
        problems.append(f"pointer count {len(sites_before)} -> {len(sites_after)}, expected +{len(appends)}")
    # Compare field values with the pointers blanked out: elements the decoder cannot split into fields
    # are compared as raw bytes, and those legitimately contain moved pointers.
    a = backend.decode(_blank(before, sites_before), include_fields=True, result=before_result)
    b = backend.decode(_blank(after, sites_after), include_fields=True, result=after_result)
    sa, sb = parc.parse_structure(before), parc.parse_structure(after)
    if sa.schema_sha256 != sb.schema_sha256:
        problems.append("schema changed")
    if [e.class_name for e in sa.toc] != [e.class_name for e in sb.toc]:
        problems.append("object list changed")
    if sb.stream_size != len(after):
        problems.append("stream size wrong")
    for x, y in zip(sorted(sb.toc, key=lambda e: e.data_offset), sorted(sb.toc, key=lambda e: e.data_offset)[1:]):
        if x.data_offset + x.data_size != y.data_offset:
            problems.append(f"gap or overlap after {x.class_name}")
            break
    for name in ("quests", "missions", "items", "inventories"):
        if getattr(a, name) != getattr(b, name):
            problems.append(f"{name} changed")
    new_knowledge = {s.values.get("_key") for s in appends if s.obj_class == "KnowledgeSaveData" and "_key" in s.values}
    if b.knowledge != a.knowledge | new_knowledge:
        problems.append(f"knowledge: expected {sorted(new_knowledge)} added, got +{sorted(b.knowledge - a.knowledge)} "
                        f"-{sorted(a.knowledge - b.knowledge)}")
    removed = set(a.fields) - set(b.fields)
    changed = {k for k in set(a.fields) & set(b.fields) if a.fields[k] != b.fields[k]}
    if removed:
        problems.append(f"{len(removed)} field(s) disappeared, e.g. {sorted(removed)[:3]}")
    if changed:
        problems.append(f"{len(changed)} field(s) changed, e.g. {sorted(changed)[:3]}")
    added = set(b.fields) - set(a.fields)
    for spec in appends:
        key_field = next((k for k in ("_key", "_knowledgeKey", "_itemKey") if k in spec.values), None)
        tag = f"{spec.list_name}[{key_field}={spec.values[key_field]}]" if key_field else spec.list_name
        mine = {k for k in added if tag in k}
        if len(mine) != len(spec.values):
            problems.append(f"{tag}: expected {len(spec.values)} new field(s), found {len(mine)}")
        added -= mine
    if added:
        problems.append(f"unexpected new field(s), e.g. {sorted(added)[:3]}")
    return problems


def append_verified(raw: bytes, backend, appends: list[ListAppend],
                    result: dict | None = None) -> tuple[bytes, EditReport]:
    result = result or backend.parse(raw)
    new, report = append_elements(raw, result, appends)
    problems = verify_append(raw, new, backend, appends, before_result=result)
    if problems:
        raise SaveEditError("verification failed: " + "; ".join(problems))
    return new, report


# --------------------------------------------------------------------------- knowledge
STORY_GRANT = 25   # _learnFromTypeRaw of entries the story grants (Flight, codex entries); used for test D


def learn_knowledge(raw: bytes, backend, keys: list[int], *, learn_from: int = STORY_GRANT) -> tuple[bytes, EditReport]:
    """Add knowledge keys to both knowledge lists, the way the game records a story grant.

    Keys already learned are skipped. The new entries are level 1, marked new, with the most recent
    learned time in the save.
    """
    result = backend.parse(raw)
    _, main = _find(result["objects"], "KnowledgeSaveData", "_list")
    have = {}
    times = []
    for e in main.list_elements:
        v = _child_values(raw, e)
        if "_key" in v:
            have[v["_key"]] = v.get("_level")
        if "_learnedFieldTime" in v:
            times.append(v["_learnedFieldTime"])
    try:
        _, learned = _find(result["objects"], "KnowledgeSaveData", "_learnedFollowLearnKnowledge")
        learned_keys = {_child_values(raw, e).get("_knowledgeKey") for e in learned.list_elements}
    except SaveEditError:
        learned_keys = None
    when = max(times, default=0)
    appends: list[ListAppend] = []
    report_skip = []
    for key in dict.fromkeys(keys):
        if key in have:
            if not have[key]:
                raise SaveEditError(f"knowledge {key} is present at level 0 (unlearned); not handled yet")
            report_skip.append(key)
            continue
        appends.append(ListAppend("KnowledgeSaveData", "_list",
                                  {"_key": key, "_level": 1, "_learnedFieldTime": when, "_isNewMark": 1}))
        if learned_keys is not None and key not in learned_keys:
            appends.append(ListAppend("KnowledgeSaveData", "_learnedFollowLearnKnowledge",
                                      {"_knowledgeKey": key, "_learnFromTypeRaw": learn_from}))
    if not appends:
        return raw, EditReport([f"knowledge {k} already learned" for k in report_skip])
    new, report = append_verified(raw, backend, appends, result)
    report.log += [f"knowledge {k} already learned" for k in report_skip]
    return new, report
