"""Optional deep-decode backend built on the NattKh Crimson Desert save editor.

The field-level decoder of the save editor (save_parser.py, MPL-2.0) is not
copied into this repository. Instead the user points Crimsonipelago at their
own checkout of https://github.com/NattKh/CRIMSON-DESERT-SAVE-EDITOR-AND-GAME-MODS
(the `CrimsonSaveEditor` folder) and this module imports it from there.
That keeps the MPL-licensed code unmodified and under its own licence.

What this backend extracts (field names as used by the editor's quest parser):
    QuestSaveData._questStateList[]     _questKey, _state, _completedTime
    QuestSaveData._missionStateList[]   _key, _state, _completeCount
    KnowledgeSaveData list              _key
    InventorySaveData._inventorylist[] _inventoryKey, _itemList[] (_itemKey, _stackCount)
plus a flat dump of every decoded field, for save-diff research.
"""

from __future__ import annotations

import importlib
import json
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REQUIRED_MODULES = ("save_parser", "save_crypto", "models")
KEY_FIELDS = ("_questKey", "_key", "_itemKey", "_knowledgeKey", "_stageKey", "_missionKey")
MAX_INLINE_BYTES = 32


class BackendUnavailable(RuntimeError):
    pass


@dataclass
class DeepState:
    quests: dict[int, dict[str, Any]] = field(default_factory=dict)
    missions: dict[int, dict[str, Any]] = field(default_factory=dict)
    knowledge: set[int] = field(default_factory=set)
    items: dict[int, int] = field(default_factory=dict)  # itemKey -> total stack over all character inventories
    inventories: dict[int, dict[int, int]] = field(default_factory=dict)  # inventoryKey -> itemKey -> stack
    fields: dict[str, str] = field(default_factory=dict)  # flat path -> hex value
    warnings: list[str] = field(default_factory=list)


@dataclass
class NameTables:
    quests: dict[int, str] = field(default_factory=dict)
    missions: dict[int, str] = field(default_factory=dict)
    knowledge: dict[int, str] = field(default_factory=dict)
    items: dict[int, str] = field(default_factory=dict)

    def label(self, table: str, key: int) -> str:
        name = getattr(self, table).get(int(key))
        return f"{key} ({name})" if name else str(key)


def _rows(doc: Any, list_key: str) -> list[dict]:
    if isinstance(doc, dict):
        doc = doc.get(list_key, [])
    return doc if isinstance(doc, list) else []


def load_name_tables(editor_dir: str | Path | None) -> NameTables:
    """Human-readable names from the user's local editor checkout, if present.

    These tables are derived from game data, so Crimsonipelago reads them from
    the user's machine instead of redistributing them.
    """
    tables = NameTables()
    if not editor_dir:
        return tables
    base = Path(editor_dir)
    specs = [
        ("quests", "quest_names.json", None, "key", ("display", "name")),
        ("missions", "mission_names.json", None, "key", ("display", "name")),
        ("knowledge", "knowledge_keys_all.json", None, "key", ("display_name", "name")),
        ("items", "item_names.json", "items", "itemKey", ("name", "internalName")),
    ]
    for attr, filename, list_key, key_field, name_fields in specs:
        path = base / filename
        if not path.is_file():
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = _rows(doc, list_key) if list_key else (doc if isinstance(doc, list) else [])
        table = getattr(tables, attr)
        for row in rows:
            if isinstance(row, dict) and key_field in row:
                label = next((row[f] for f in name_fields if row.get(f)), None)
                if label:
                    table[int(row[key_field])] = str(label)
    return tables


class NattKhBackend:
    name = "nattkh"

    def __init__(self, editor_dir: str | Path):
        self.editor_dir = Path(editor_dir)
        missing = [m for m in REQUIRED_MODULES if not (self.editor_dir / f"{m}.py").is_file()]
        if missing:
            raise BackendUnavailable(
                f"{self.editor_dir} does not look like the CrimsonSaveEditor folder (missing {', '.join(missing)}.py)"
            )
        self._parser = self._import_parser()

    def _import_parser(self):
        path = str(self.editor_dir)
        saved = {m: sys.modules.pop(m) for m in REQUIRED_MODULES if m in sys.modules}
        sys.path.insert(0, path)
        try:
            return importlib.import_module("save_parser")
        except Exception as exc:  # the editor has optional native deps; report instead of crashing
            raise BackendUnavailable(f"could not import the editor's save_parser: {exc}") from exc
        finally:
            sys.path.remove(path)
            sys.modules.update({k: v for k, v in saved.items() if k not in sys.modules})

    # ------------------------------------------------------------------ decoding
    def decode(self, raw: bytes, *, include_fields: bool = False) -> DeepState:
        # The editor's legacy item scanner misses most of the inventory on current builds (found in the
        # first real research session), so items are read from the parsed InventorySaveData tree instead.
        result = self._parser.build_result_from_raw(raw, {"input_kind": "raw_blob"}, include_legacy=False)
        state = DeepState()
        for obj in result["objects"]:
            if obj.class_name == "QuestSaveData":
                self._read_quests(raw, obj, state)
            elif obj.class_name == "KnowledgeSaveData":
                self._read_knowledge(raw, obj, state)
            elif obj.class_name == "InventorySaveData":
                self._read_inventory(raw, obj, state)
        if include_fields:
            # Number objects per class, not by position: positions shift whenever the game adds new
            # save objects, which made paths incomparable between saves.
            seen: dict[str, int] = {}
            for obj in result["objects"]:
                n = seen.get(obj.class_name, 0)
                seen[obj.class_name] = n + 1
                self._flatten(raw, obj.fields, f"{obj.class_name}#{n}", state.fields)
        return state

    @staticmethod
    def _scalar(raw: bytes, f) -> int | None:
        if not f.present:
            return None
        size = f.end_offset - f.start_offset
        fmt = {1: "<B", 2: "<H", 4: "<I", 8: "<Q"}.get(size)
        return struct.unpack_from(fmt, raw, f.start_offset)[0] if fmt else None

    def _children(self, raw: bytes, elem) -> dict[str, int | None]:
        return {cf.name: self._scalar(raw, cf) for cf in (elem.child_fields or []) if cf.present}

    def _read_quests(self, raw: bytes, obj, state: DeepState) -> None:
        for f in obj.fields:
            if not f.present or not f.list_elements:
                continue
            if f.name == "_questStateList":
                for elem in f.list_elements:
                    c = self._children(raw, elem)
                    if c.get("_questKey") is not None:
                        state.quests[c["_questKey"]] = {"state": c.get("_state"), "completed_time": c.get("_completedTime")}
            elif f.name == "_missionStateList":
                for elem in f.list_elements:
                    c = self._children(raw, elem)
                    if c.get("_key") is not None:
                        state.missions[c["_key"]] = {
                            "state": c.get("_state"),
                            "complete_count": c.get("_completeCount"),
                            "completed_time": c.get("_completedTime"),
                        }

    def _read_inventory(self, raw: bytes, obj, state: DeepState) -> None:
        for f in obj.fields:
            if not f.present or f.name != "_inventorylist":
                continue
            for bag in f.list_elements or []:
                fields = {cf.name: cf for cf in (bag.child_fields or []) if cf.present}
                inv_key = self._scalar(raw, fields["_inventoryKey"]) if "_inventoryKey" in fields else None
                contents = state.inventories.setdefault(inv_key if inv_key is not None else -1, {})
                for elem in (fields["_itemList"].list_elements or []) if "_itemList" in fields else []:
                    c = self._children(raw, elem)
                    key, stack = c.get("_itemKey"), c.get("_stackCount")
                    if key is None:
                        continue
                    stack = 1 if stack is None else int(stack)
                    contents[key] = contents.get(key, 0) + stack
                    state.items[key] = state.items.get(key, 0) + stack

    def _read_knowledge(self, raw: bytes, obj, state: DeepState) -> None:
        for f in obj.fields:
            if f.present and f.name in ("_knowledgeElementSaveDataList", "_list"):
                for elem in f.list_elements or []:
                    key = self._children(raw, elem).get("_key")
                    if key is not None:
                        state.knowledge.add(key)

    def _flatten(self, raw: bytes, fields, prefix: str, out: dict[str, str]) -> None:
        for f in fields or []:
            if not f.present:
                continue
            path = f"{prefix}.{f.name}"
            if f.list_elements:
                for i, elem in enumerate(f.list_elements):
                    children = {cf.name: cf for cf in (elem.child_fields or []) if cf.present}
                    key_field = next((k for k in KEY_FIELDS if k in children), None)
                    tag = f"[{key_field}={self._scalar(raw, children[key_field])}]" if key_field else f"[{i}]"
                    if elem.child_fields:
                        self._flatten(raw, elem.child_fields, path + tag, out)
                    else:
                        out[path + tag] = self._hex(raw, elem.start_offset, elem.end_offset)
            elif f.child_fields:
                self._flatten(raw, f.child_fields, path, out)
            else:
                out[path] = self._hex(raw, f.start_offset, f.end_offset)

    @staticmethod
    def _hex(raw: bytes, start: int, end: int) -> str:
        chunk = raw[start:end]
        if len(chunk) <= MAX_INLINE_BYTES:
            return chunk.hex()
        import hashlib
        return f"<{len(chunk)} bytes sha256:{hashlib.sha256(chunk).hexdigest()[:16]}>"
