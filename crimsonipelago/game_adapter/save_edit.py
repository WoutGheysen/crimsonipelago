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

New elements are copies of an existing element of the same list. By default the copied entry must have
exactly the fields being set, so nothing is inherited by accident; inventory items instead copy an entry
of the same layout, which differs from every other one only in the fields that are set (measured on all
saves collected so far, see give_items).

The result is decoded again and compared with the original: the edit fails unless the only differences
are the new elements and the counts.
"""

from __future__ import annotations

import struct
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from . import parc

SENTINEL = b"\xff" * 8
_FMT = {1: "<B", 2: "<H", 4: "<I", 8: "<Q"}


class SaveEditError(ValueError):
    pass


@dataclass(frozen=True)
class ListAppend:
    """Append one element to a list, with these child field values.

    `list_name` is a field of `obj_class`, or a path into nested lists such as
    "_inventorylist[_inventoryKey=2]._itemList" (the bag whose _inventoryKey is 2).
    Without `template`, the copied element must have exactly the fields in `values`. With it, `template`
    picks the element to copy, and `raw_patches` (offset in the element, bytes) are written after `values`.
    `tag` is how the new element shows up in the decoded field paths (default: its key field)."""
    obj_class: str
    list_name: str
    values: dict[str, int]
    template: Callable[[Any], bool] | None = None
    raw_patches: tuple[tuple[int, bytes], ...] = ()
    tag: str | None = None


@dataclass
class EditReport:
    log: list[str] = field(default_factory=list)
    growth: int = 0
    new_pointers: int = 0


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


def _find(objects, cls: str, list_name: str, raw: bytes | None = None):
    objs = [o for o in objects if o.class_name == cls]
    if len(objs) != 1:
        raise SaveEditError(f"expected one {cls} in the save, found {len(objs)}")
    fields = objs[0].fields
    segments = list_name.split(".")
    for i, segment in enumerate(segments):
        name, _, selector = segment.partition("[")
        found = [f for f in fields if f.present and f.name == name]
        if len(found) != 1:
            raise SaveEditError(f"{cls}.{list_name}: {name} not found")
        f = found[0]
        if not selector:
            if i != len(segments) - 1:
                fields = f.child_fields or []
                continue
            if f.list_elements is None:
                raise SaveEditError(f"{cls}.{list_name} is not a list")
            return objs[0], f
        key, _, value = selector.rstrip("]").partition("=")
        if raw is None:
            raise SaveEditError("selecting a list element needs the save data")
        matches = [e for e in f.list_elements or [] if _child_values(raw, e).get(key) == int(value)]
        if len(matches) != 1:
            raise SaveEditError(f"{cls}.{list_name}: {len(matches)} elements match {key}={value}")
        fields = matches[0].child_fields or []
    raise SaveEditError(f"{cls}.{list_name} does not end in a list")


def _clone(raw: bytes, template, values: dict[str, int], new_pos: int,
           raw_patches: tuple[tuple[int, bytes], ...] = ()) -> bytes:
    start, end = template.start_offset, template.end_offset
    data = bytearray(raw[start:end])
    present = {cf.name: cf for cf in template.child_fields or [] if cf.present}
    for name, value in values.items():
        cf = present[name]
        fmt = _FMT.get(cf.end_offset - cf.start_offset)
        if fmt is None:
            raise SaveEditError(f"field {name} has an unsupported size")
        struct.pack_into(fmt, data, cf.start_offset - start, value)
    for offset, patch in raw_patches:
        if not 0 <= offset <= len(data) - len(patch):
            raise SaveEditError("internal error: patch outside the element")
        data[offset:offset + len(patch)] = patch
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
        obj, lst = _find(objects, spec.obj_class, spec.list_name, raw)
        count_pos, count = _list_header(raw, lst)
        if lst.end_offset != (lst.list_elements[-1].end_offset if lst.list_elements else lst.start_offset + lst.list_header_size):
            raise SaveEditError(f"{spec.list_name}: bytes after the last element; layout not understood")
        wanted = set(spec.values)
        if spec.template is not None:
            candidates = [e for e in lst.list_elements if e.child_fields is not None and spec.template(e)
                          and wanted <= {cf.name for cf in e.child_fields if cf.present}]
        else:
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
        report.new_pointers += sum(1 for _ in _containers(template))
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
    struct.pack_into("<I", work, structure.schema_end + 8, structure.stream_size + total)  # = blob length in real saves

    # ---- splice
    out = bytearray()
    cursor = 0
    by_offset: dict[int, list] = {}
    for offset, template, spec, _ in inserts:
        by_offset.setdefault(offset, []).append((template, spec))
    for off in offsets:
        out += work[cursor:off]
        for template, spec in by_offset[off]:
            out += _clone(raw, template, spec.values, len(out), spec.raw_patches)
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


@dataclass
class Expected:
    """What an edit is allowed to change, besides adding the requested elements."""
    new_pointers: int = 0
    items: dict[int, int] | None = None                       # item key -> total after the edit (None: unchanged)
    changed_fields: Callable[[str], bool] = lambda path: False  # decoded paths allowed to change value


def _tag(spec: ListAppend) -> str:
    if spec.tag:
        return spec.tag
    last = spec.list_name.split(".")[-1].partition("[")[0]
    key_field = next((k for k in ("_key", "_knowledgeKey", "_itemKey") if k in spec.values), None)
    return f"{last}[{key_field}={spec.values[key_field]}]" if key_field else last


def verify_append(before: bytes, after: bytes, backend, appends: list[ListAppend],
                  before_result: dict | None = None, expected: Expected | None = None) -> list[str]:
    """Differences between the two blobs other than the requested new elements."""
    expected = expected or Expected(new_pointers=len(appends))
    problems: list[str] = []
    before_result = before_result or backend.parse(before)
    after_result = backend.parse(after)
    try:
        sites_before = pointer_sites(before, before_result["objects"])
        sites_after = pointer_sites(after, after_result["objects"])
    except SaveEditError as exc:
        return [str(exc)]
    if len(sites_after) != len(sites_before) + expected.new_pointers:
        problems.append(f"pointer count {len(sites_before)} -> {len(sites_after)}, expected +{expected.new_pointers}")
    # Compare field values with the pointers blanked out: elements the decoder cannot split into fields
    # are compared as raw bytes, and those legitimately contain moved pointers.
    a = backend.decode(_blank(before, sites_before), include_fields=True, result=before_result)
    b = backend.decode(_blank(after, sites_after), include_fields=True, result=after_result)
    sa, sb = parc.parse_structure(before), parc.parse_structure(after)
    if sa.schema_sha256 != sb.schema_sha256:
        problems.append("schema changed")
    if [e.class_name for e in sa.toc] != [e.class_name for e in sb.toc]:
        problems.append("object list changed")
    if sb.stream_size - len(after) != sa.stream_size - len(before):
        problems.append("stream size wrong")
    for x, y in zip(sorted(sb.toc, key=lambda e: e.data_offset), sorted(sb.toc, key=lambda e: e.data_offset)[1:]):
        if x.data_offset + x.data_size != y.data_offset:
            problems.append(f"gap or overlap after {x.class_name}")
            break
    for name in ("quests", "missions"):
        if getattr(a, name) != getattr(b, name):
            problems.append(f"{name} changed")
    want_items = a.items if expected.items is None else expected.items
    if b.items != want_items:
        diff = {k: (want_items.get(k), b.items.get(k)) for k in set(want_items) | set(b.items)
                if want_items.get(k) != b.items.get(k)}
        problems.append(f"items differ from what was expected (key: expected, found): {diff}")
    new_knowledge = {s.values.get("_key") for s in appends if s.obj_class == "KnowledgeSaveData" and "_key" in s.values}
    if b.knowledge != a.knowledge | new_knowledge:
        problems.append(f"knowledge: expected {sorted(new_knowledge)} added, got +{sorted(b.knowledge - a.knowledge)} "
                        f"-{sorted(a.knowledge - b.knowledge)}")
    removed = set(a.fields) - set(b.fields)
    changed = {k for k in set(a.fields) & set(b.fields) if a.fields[k] != b.fields[k] and not expected.changed_fields(k)}
    if removed:
        problems.append(f"{len(removed)} field(s) disappeared, e.g. {sorted(removed)[:3]}")
    if changed:
        problems.append(f"{len(changed)} field(s) changed, e.g. {sorted(changed)[:3]}")
    added = set(b.fields) - set(a.fields)
    for spec in appends:
        tag = _tag(spec)
        mine = {k for k in added if f".{tag}." in k or k.endswith(f".{tag}")}
        if spec.template is None and len(mine) != len(spec.values):
            problems.append(f"{tag}: expected {len(spec.values)} new field(s), found {len(mine)}")
        for name, value in spec.values.items():
            hits = [k for k in mine if k.endswith(f".{tag}.{name}")]
            if len(hits) != 1 or int.from_bytes(bytes.fromhex(b.fields[hits[0]]), "little") != value:
                problems.append(f"{tag}.{name}: expected {value} in the new element")
        added -= mine
    if added:
        problems.append(f"unexpected new field(s), e.g. {sorted(added)[:3]}")
    return problems


def append_verified(raw: bytes, backend, appends: list[ListAppend],
                    result: dict | None = None) -> tuple[bytes, EditReport]:
    result = result or backend.parse(raw)
    new, report = append_elements(raw, result, appends)
    problems = verify_append(raw, new, backend, appends, before_result=result,
                             expected=Expected(new_pointers=report.new_pointers))
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


# --------------------------------------------------------------------------- inventory items
MAIN_BAG = 2                      # the player's main bag (bag 5 holds quest items)
MISC_LAYOUT = bytes.fromhex("1f515a")
ITEM_NO_BASE = 900_000_000        # item numbers handed out by Crimsonipelago: far above the game's own counter
_UNIX_RANGE = (1_600_000_000, 2_500_000_000)


@dataclass(frozen=True)
class Gift:
    item_key: int
    count: int
    max_stack: int | None = None


def _item_elements(raw: bytes, result: dict) -> tuple[Any, list[tuple[Any, dict]]]:
    _, items = _find(result["objects"], "InventorySaveData", f"_inventorylist[_inventoryKey={MAIN_BAG}]._itemList", raw)
    return items, [(e, _child_values(raw, e)) for e in items.list_elements]


def _all_item_numbers(raw: bytes, result: dict) -> set[int]:
    out = set()
    for obj in result["objects"]:
        for node in _walk(obj.fields):
            if node.name == "_itemNo" and node.end_offset - node.start_offset == 8:
                out.add(struct.unpack_from("<Q", raw, node.start_offset)[0])
    return out


def _misc_patches(raw: bytes, template, item_key: int, now: int) -> tuple[tuple[int, bytes], ...]:
    """Raw patches for the fields after the socket list, whose decoded offsets are not reliable.

    Measured layout of a misc item's tail: socket list, 01 01, _transferredItemKey (= the item key),
    charge fields, _timeWhenPushItem (Unix seconds), [_isNewMark], trailing size."""
    start, end = template.start_offset, template.end_offset
    data = raw[start:end]
    fields = {cf.name: cf for cf in template.child_fields if cf.present}
    old_key = _child_values(raw, template)["_itemKey"]
    sockets = fields.get("_socketSaveDataList")
    if sockets is None:
        raise SaveEditError("item layout not understood (no socket list)")
    after = sockets.end_offset - start
    if data[after:after + 2] != b"\x01\x01" or _u32(data, after + 2) != old_key:
        raise SaveEditError("item layout not understood (transferred item key)")
    has_new_mark = len(template.child_mask_bytes) > 3 and template.child_mask_bytes[3] & 0x01
    time_at = len(data) - 4 - (1 if has_new_mark else 0) - 8
    if not _UNIX_RANGE[0] < struct.unpack_from("<Q", data, time_at)[0] < _UNIX_RANGE[1]:
        raise SaveEditError("item layout not understood (time the item was received)")
    patches = [(after + 2, struct.pack("<I", item_key)), (time_at, struct.pack("<Q", now))]
    if has_new_mark:
        patches.append((len(data) - 5, b"\x01"))
    return tuple(patches)


def give_items(raw: bytes, backend, gifts: list[Gift], *, now: int | None = None) -> tuple[bytes, EditReport]:
    """Add items to the main bag the way the game does: onto an existing stack, or as a new stack in the
    lowest free slot. New stacks copy an item of the same layout ("misc": money, Abyss Artifacts, most
    materials), which differs from every other one only in the fields set here.

    A gift that would push a stack past its limit is refused rather than split (for now)."""
    import time

    now = int(time.time()) if now is None else now
    result = backend.parse(raw)
    before_items = backend.decode(raw, result=result).items
    _, elements = _item_elements(raw, result)
    by_key: dict[int, list[tuple[Any, dict]]] = {}
    for e, v in elements:
        by_key.setdefault(v.get("_itemKey"), []).append((e, v))
    used_slots = {v.get("_slotNo") for _, v in elements}
    numbers = _all_item_numbers(raw, result)
    # 0xFFFF... marks "no item"; the game's own numbers stay far below the base
    next_no = max([n for n in numbers if ITEM_NO_BASE <= n < 1 << 40], default=ITEM_NO_BASE) + 1

    work = bytearray(raw)
    stacked: set[int] = set()
    appends: list[ListAppend] = []
    expected = dict(before_items)
    report_log: list[str] = []
    merged: dict[int, Gift] = {}
    for g in gifts:
        m = merged.get(g.item_key)
        merged[g.item_key] = Gift(g.item_key, (m.count if m else 0) + g.count, g.max_stack)
    for gift in merged.values():
        if gift.count <= 0:
            continue
        stacks = by_key.get(gift.item_key, [])
        if len(stacks) > 1:
            raise SaveEditError(f"item {gift.item_key}: several stacks in the bag; not handled yet")
        if stacks:
            e, v = stacks[0]
            new_count = v["_stackCount"] + gift.count
            if gift.max_stack and new_count > gift.max_stack:
                raise SaveEditError(f"item {gift.item_key}: the stack is full ({v['_stackCount']} + {gift.count} would pass "
                                    f"the limit of {gift.max_stack})")
            cf = next(cf for cf in e.child_fields if cf.name == "_stackCount")
            struct.pack_into(_FMT[cf.end_offset - cf.start_offset], work, cf.start_offset, new_count)
            stacked.add(gift.item_key)
            report_log.append(f"item {gift.item_key}: stack {v['_stackCount']} -> {new_count}")
        else:
            if gift.max_stack and gift.count > gift.max_stack:
                raise SaveEditError(f"item {gift.item_key}: {gift.count} is more than one stack ({gift.max_stack})")
            templates = [e for e, _ in elements if e.child_mask_bytes[:3] == MISC_LAYOUT]
            if not templates:
                raise SaveEditError("no item of the right kind in the bag to copy yet")
            template = max(templates, key=lambda e: (e.child_mask_bytes[3:4] == b"\x01", e.start_offset))
            slot = next(i for i in range(10_000) if i not in used_slots)
            used_slots.add(slot)
            values = {"_itemNo": next_no, "_itemKey": gift.item_key, "_slotNo": slot, "_stackCount": gift.count}
            next_no += 1
            appends.append(ListAppend("InventorySaveData", f"_inventorylist[_inventoryKey={MAIN_BAG}]._itemList", values,
                                      template=lambda e, t=template: e is t,
                                      raw_patches=_misc_patches(raw, template, gift.item_key, now)))
            report_log.append(f"item {gift.item_key}: new stack of {gift.count} in slot {slot}")
        expected[gift.item_key] = expected.get(gift.item_key, 0) + gift.count

    if appends:
        new, report = append_elements(bytes(work), result, appends)
    else:
        new, report = bytes(work), EditReport()
    exp = Expected(new_pointers=report.new_pointers, items=expected,
                   changed_fields=lambda path: any(path.endswith(f"._itemList[_itemKey={k}]._stackCount") for k in stacked))
    problems = verify_append(raw, new, backend, appends, before_result=result, expected=exp)
    if problems:
        raise SaveEditError("verification failed: " + "; ".join(problems))
    report.log = report_log + report.log
    return new, report
