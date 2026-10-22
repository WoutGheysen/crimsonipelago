"""crimson-testsave: make an edited COPY of a save for in-game write experiments (Phase 0, scope doc 3.4).

Only same-size, in-place edits are supported, so the save's structure is never re-serialised:

    --stack ITEM=COUNT          set the stack count of an item already in an inventory bag
    --swap-knowledge OLD:NEW    replace knowledge key OLD with NEW (NEW must not already be present).
                                Swapping an ability to a harmless codex key removes the ability
                                without changing the list length.

One structural edit, the same one the client uses to deliver abilities:

    --learn KEY                 learn knowledge KEY (added to both knowledge lists, as the story does)

The source file is never modified. The output is re-encoded (LZ4 + ChaCha20 + HMAC) exactly as the
save editor does, then decoded again and compared with the source: the command fails unless the
only differences are the requested ones.
"""

from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..game_adapter import save_container, snapshot
from ..game_adapter.nattkh_backend import NattKhBackend


class EditError(ValueError):
    pass


@dataclass
class EditPlan:
    stacks: dict[int, int] = field(default_factory=dict)          # itemKey -> new total stack
    knowledge_swaps: dict[int, int] = field(default_factory=dict)  # old key -> new key
    learn: list[int] = field(default_factory=list)                  # knowledge keys to add


def _fields(elem) -> dict:
    return {cf.name: cf for cf in (elem.child_fields or []) if cf.present}


def _patch_uint(blob: bytearray, f, value: int) -> int:
    size = f.end_offset - f.start_offset
    fmt = {1: "<B", 2: "<H", 4: "<I", 8: "<Q"}.get(size)
    if fmt is None:
        raise EditError(f"field {f.name} has unsupported size {size}")
    old = struct.unpack_from(fmt, blob, f.start_offset)[0]
    struct.pack_into(fmt, blob, f.start_offset, value)
    return old


def apply_edits(raw: bytes, plan: EditPlan, backend: NattKhBackend) -> tuple[bytes, list[str]]:
    blob = bytearray(raw)
    result = backend._parser.build_result_from_raw(bytes(raw), {"input_kind": "raw_blob"}, include_legacy=False)
    log: list[str] = []
    stacks_left, swaps_left = dict(plan.stacks), dict(plan.knowledge_swaps)

    for obj in result["objects"]:
        if obj.class_name == "InventorySaveData" and stacks_left:
            for f in obj.fields:
                if not f.present or f.name != "_inventorylist":
                    continue
                for bag in f.list_elements or []:
                    bf = _fields(bag)
                    bag_key = backend._scalar(raw, bf["_inventoryKey"]) if "_inventoryKey" in bf else None
                    for item in (bf["_itemList"].list_elements or []) if "_itemList" in bf else []:
                        it = _fields(item)
                        key = backend._scalar(raw, it["_itemKey"]) if "_itemKey" in it else None
                        if key in stacks_left and "_stackCount" in it:
                            old = _patch_uint(blob, it["_stackCount"], stacks_left.pop(key))
                            log.append(f"item {key} in bag {bag_key}: stack {old} -> {plan.stacks[key]}")
        elif obj.class_name == "KnowledgeSaveData" and swaps_left:
            # A learned ability is recorded twice: in the knowledge list (_list[]._key) and in the
            # "learned" record (_learnedFollowLearnKnowledge[]._knowledgeKey), from which the game
            # restores the list entry on load (found in write test B, 2026-10-21). Swap both.
            lists: dict[str, list] = {}
            for f in obj.fields:
                if not f.present or not f.list_elements:
                    continue
                key_name = {"_list": "_key", "_knowledgeElementSaveDataList": "_key",
                            "_learnedFollowLearnKnowledge": "_knowledgeKey"}.get(f.name)
                if key_name is None:
                    continue
                for elem in f.list_elements:
                    kf = _fields(elem).get(key_name)
                    if kf is not None:
                        lists.setdefault(f.name, []).append((backend._scalar(raw, kf), kf))
            for old_key, new_key in list(swaps_left.items()):
                swapped = False
                for list_name, entries in lists.items():
                    if any(k == new_key for k, _ in entries):
                        raise EditError(f"knowledge {new_key} is already in the save ({list_name}); pick a key that is absent")
                    for k, kf in entries:
                        if k == old_key:
                            _patch_uint(blob, kf, new_key)
                            log.append(f"knowledge {old_key} -> {new_key} in {list_name}")
                            swapped = True
                if swapped:
                    swaps_left.pop(old_key)

    if stacks_left:
        raise EditError(f"item(s) not found in any inventory bag: {sorted(stacks_left)}")
    if swaps_left:
        raise EditError(f"knowledge key(s) not found: {sorted(swaps_left)}")
    if plan.learn:
        from ..game_adapter import save_edit
        try:
            new, report = save_edit.learn_knowledge(bytes(blob), backend, plan.learn)
        except save_edit.SaveEditError as exc:
            raise EditError(str(exc)) from exc
        log += report.log
        return new, log
    return bytes(blob), log


def verify(before: dict, after: dict, plan: EditPlan) -> list[str]:
    """Differences between two snapshots that the plan did not ask for."""
    problems = []
    a, b = before["state"], after["state"]
    expect_items = {str(k): v for k, v in plan.stacks.items()}
    for k in set(a["items"]) | set(b["items"]):
        want = expect_items.get(k, a["items"].get(k))
        if b["items"].get(k) != want:
            problems.append(f"item {k}: expected {want}, got {b['items'].get(k)}")
    ka, kb = set(a["knowledge"]), set(b["knowledge"])
    expect_k = (ka - set(plan.knowledge_swaps)) | set(plan.knowledge_swaps.values()) | set(plan.learn)
    if kb != expect_k:
        problems.append(f"knowledge differs: missing {sorted(expect_k - kb)}, extra {sorted(kb - expect_k)}")
    for name in ("quests", "missions"):
        if a[name] != b[name]:
            problems.append(f"{name} changed")
    if before["structure"]["schema_sha256"] != after["structure"]["schema_sha256"]:
        problems.append("schema changed")
    if [o["class"] for o in before["structure"]["objects"]] != [o["class"] for o in after["structure"]["objects"]]:
        problems.append("object list changed")
    return problems


def make_test_save(src: Path, out: Path, plan: EditPlan, editor_dir: Path) -> list[str]:
    if out.resolve() == src.resolve():
        raise EditError("refusing to overwrite the source save; write the test copy somewhere else")
    data = src.read_bytes()
    container = save_container.decode(data)
    backend = NattKhBackend(editor_dir)
    new_blob, log = apply_edits(container.blob, plan, backend)
    encoded = save_container.encode(new_blob, container.header)
    # verify by reading the encoded file back exactly as the inspector would
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_bytes(encoded)
    try:
        before = snapshot.snapshot_file(src, backend=backend)
        after = snapshot.snapshot_file(tmp, backend=backend)
        if not after["container"]["hmac_ok"]:
            raise EditError("re-encoded save fails its HMAC check")
        problems = verify(before, after, plan)
        if problems:
            raise EditError("verification failed: " + "; ".join(problems))
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    return log


def _pair(text: str, sep: str) -> tuple[int, int]:
    a, b = text.split(sep, 1)
    return int(a), int(b)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="crimson-testsave", description=__doc__.splitlines()[0])
    p.add_argument("source", type=Path, help="save.save to copy (never modified)")
    p.add_argument("output", type=Path, help="where to write the edited copy")
    p.add_argument("--editor-dir", type=Path, required=True, help="your CrimsonSaveEditor folder")
    p.add_argument("--stack", action="append", default=[], metavar="ITEM=COUNT")
    p.add_argument("--swap-knowledge", action="append", default=[], metavar="OLD:NEW")
    p.add_argument("--learn", action="append", default=[], type=int, metavar="KEY")
    args = p.parse_args(argv)
    plan = EditPlan(stacks=dict(_pair(s, "=") for s in args.stack),
                    knowledge_swaps=dict(_pair(s, ":") for s in args.swap_knowledge), learn=args.learn)
    if not plan.stacks and not plan.knowledge_swaps and not plan.learn:
        p.error("nothing to do: give --stack, --swap-knowledge and/or --learn")
    try:
        log = make_test_save(args.source, args.output, plan, args.editor_dir)
    except (EditError, save_container.SaveFormatError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for line in log:
        print(line)
    print(f"verified and wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
