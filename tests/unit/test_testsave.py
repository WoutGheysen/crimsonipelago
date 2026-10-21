"""crimson-testsave: in-place edits, verification and safety refusals (no real save needed)."""

import struct
from dataclasses import dataclass, field

import pytest

from crimsonipelago.tools import testsave as test_edit
from crimsonipelago.tools.testsave import EditError, EditPlan


@dataclass
class F:  # stand-in for the editor parser's GenericFieldValue
    name: str
    start_offset: int = 0
    end_offset: int = 0
    present: bool = True
    list_elements: list = field(default_factory=list)
    child_fields: list = field(default_factory=list)


@dataclass
class Obj:
    class_name: str
    fields: list


class FakeBackend:
    """Mimics NattKhBackend for a hand-built blob: one bag with one item, two knowledge entries."""

    def __init__(self, raw):
        self.raw = raw

        class P:
            @staticmethod
            def build_result_from_raw(raw, meta, include_legacy=False):
                item = F("[0]", child_fields=[F("_itemKey", 0, 4), F("_stackCount", 4, 12)])
                bag = F("[0]", child_fields=[F("_inventoryKey", 12, 14), F("_itemList", list_elements=[item])])
                inv = Obj("InventorySaveData", [F("_inventorylist", list_elements=[bag])])
                k1 = F("[0]", child_fields=[F("_key", 14, 18)])
                k2 = F("[1]", child_fields=[F("_key", 18, 22)])
                know = Obj("KnowledgeSaveData", [F("_list", list_elements=[k1, k2])])
                return {"objects": [inv, know]}

        self._parser = P()

    @staticmethod
    def _scalar(raw, f):
        size = f.end_offset - f.start_offset
        return struct.unpack_from({2: "<H", 4: "<I", 8: "<Q"}[size], raw, f.start_offset)[0]


RAW = struct.pack("<IQHII", 75001, 2, 2, 40002, 40068)  # item 75001 x2 in bag 2; knowledge 40002, 40068


def test_stack_and_knowledge_edits_are_in_place():
    new, log = test_edit.apply_edits(RAW, EditPlan(stacks={75001: 3}, knowledge_swaps={40002: 1006}), FakeBackend(RAW))
    assert len(new) == len(RAW)
    assert struct.unpack("<IQHII", new) == (75001, 3, 2, 1006, 40068)
    assert log == ["item 75001 in bag 2: stack 2 -> 3", "knowledge 40002 -> 1006"]


def test_refuses_duplicate_or_missing_keys():
    with pytest.raises(EditError, match="already in the save"):
        test_edit.apply_edits(RAW, EditPlan(knowledge_swaps={40002: 40068}), FakeBackend(RAW))
    with pytest.raises(EditError, match="not found"):
        test_edit.apply_edits(RAW, EditPlan(knowledge_swaps={12345: 1006}), FakeBackend(RAW))
    with pytest.raises(EditError, match="not found in any inventory"):
        test_edit.apply_edits(RAW, EditPlan(stacks={99: 1}), FakeBackend(RAW))


def _snap(items, knowledge, quests=None):
    return {"state": {"items": items, "knowledge": knowledge, "quests": quests or {}, "missions": {}},
            "structure": {"schema_sha256": "s", "objects": [{"class": "A"}]}}


def test_verify_accepts_only_requested_changes():
    plan = EditPlan(stacks={75001: 3}, knowledge_swaps={40002: 1006})
    before = _snap({"75001": 2, "1": 5}, [40002, 7])
    assert test_edit.verify(before, _snap({"75001": 3, "1": 5}, [1006, 7]), plan) == []
    problems = test_edit.verify(before, _snap({"75001": 3, "1": 4}, [1006, 7], quests={"1": 5}), plan)
    assert any("item 1" in p for p in problems) and "quests changed" in problems


def test_never_overwrites_the_source(tmp_path):
    src = tmp_path / "save.save"
    src.write_bytes(b"x")
    with pytest.raises(EditError, match="refusing to overwrite"):
        test_edit.make_test_save(src, src, EditPlan(stacks={1: 1}), tmp_path)
