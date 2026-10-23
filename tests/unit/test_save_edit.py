"""Structural save edits (learning knowledge), on a synthetic save with the real list layout.

Skipped unless NATTKH_EDITOR_DIR points at a CrimsonSaveEditor checkout: the edit and its verification
use the editor's decoder.
"""

import struct

import pytest

from crimsonipelago.game_adapter import parc, save_edit
from crimsonipelago.game_adapter.nattkh_backend import NattKhBackend
from tests.conftest import editor_path
from tests.fixtures.synthetic_save import make_knowledge_blob

EDITOR = editor_path()
pytestmark = pytest.mark.skipif(EDITOR is None, reason="NATTKH_EDITOR_DIR not set")


@pytest.fixture(scope="module")
def backend():
    return NattKhBackend(EDITOR)


def _lists(backend, raw):
    res = backend.parse(raw)
    know = next(o for o in res["objects"] if o.class_name == "KnowledgeSaveData")
    out = {}
    for f in know.fields:
        if f.present and f.list_elements is not None:
            out[f.name] = [save_edit._child_values(raw, e) for e in f.list_elements]
    return res, out


def test_learn_adds_to_both_lists(backend):
    raw = make_knowledge_blob()
    new, report = save_edit.learn_knowledge(raw, backend, [40002])
    _, lists = _lists(backend, new)
    assert lists["_list"][-1] == {"_key": 40002, "_level": 1, "_learnedFieldTime": 6000, "_isNewMark": 1}
    assert lists["_learnedFollowLearnKnowledge"][-1] == {"_knowledgeKey": 40002, "_learnFromTypeRaw": 25}
    assert [e["_key"] for e in lists["_list"][:2]] == [101, 102]
    assert report.growth == 43 + 31
    assert backend.decode(new).quests == backend.decode(raw).quests


def test_pointers_tables_and_sizes_stay_consistent(backend):
    raw = make_knowledge_blob()
    new, _ = save_edit.learn_knowledge(raw, backend, [40002, 40082])
    res = backend.parse(new)
    sites = save_edit.pointer_sites(new, res["objects"])          # raises if any pointer is off
    assert len(sites) == len(save_edit.pointer_sites(raw, backend.parse(raw)["objects"])) + 4
    st = parc.parse_structure(new)
    assert st.stream_size - len(new) == parc.parse_structure(raw).stream_size - len(raw)  # grows with the blob
    ordered = sorted(st.toc, key=lambda e: e.data_offset)
    assert ordered[-1].data_offset + ordered[-1].data_size == len(new)
    assert all(a.data_offset + a.data_size == b.data_offset for a, b in zip(ordered, ordered[1:]))
    # the quest list after the knowledge block moved, and its elements still decode
    assert backend.decode(new).quests[901]["completed_time"] == 4500


def test_already_learned_is_a_no_op(backend):
    raw = make_knowledge_blob()
    new, report = save_edit.learn_knowledge(raw, backend, [101])
    assert new == raw
    assert "already learned" in report.log[0]
    once, _ = save_edit.learn_knowledge(raw, backend, [40002])
    twice, _ = save_edit.learn_knowledge(once, backend, [40002])
    assert twice == once


def test_unlearned_entry_is_refused(backend):
    raw = make_knowledge_blob(knowledge=((101, 1, 5000), (40002, 0, 6000)))
    with pytest.raises(save_edit.SaveEditError, match="level 0"):
        save_edit.learn_knowledge(raw, backend, [40002])


def test_no_entry_to_copy_is_refused(backend):
    raw = make_knowledge_blob(learned=())
    with pytest.raises(save_edit.SaveEditError, match="no existing entry|unknown header layout"):
        save_edit.learn_knowledge(raw, backend, [40002])


def test_verification_catches_a_broken_edit(backend):
    raw = make_knowledge_blob()
    appends = [save_edit.ListAppend("KnowledgeSaveData", "_list",
                                    {"_key": 7, "_level": 1, "_learnedFieldTime": 1, "_isNewMark": 1})]
    new, _ = save_edit.append_elements(raw, backend.parse(raw), appends)
    assert save_edit.verify_append(raw, new, backend, appends) == []
    # damage the quest object's stored value: verification must notice the unrequested change
    broken = bytearray(new)
    pos = broken.rfind(struct.pack("<I", 4500))
    struct.pack_into("<I", broken, pos, 4501)
    assert any("changed" in p for p in save_edit.verify_append(raw, bytes(broken), backend, appends))


# --------------------------------------------------------------------------- inventory items
from tests.fixtures.synthetic_save import make_inventory_blob  # noqa: E402

NOW = 1_792_700_000


def _bag(backend, raw):
    res = backend.parse(raw)
    _, items = save_edit._find(res["objects"], "InventorySaveData", "_inventorylist[_inventoryKey=2]._itemList", raw)
    return [save_edit._child_values(raw, e) for e in items.list_elements], items.list_elements


def test_give_adds_a_new_stack_like_the_game(backend):
    raw = make_inventory_blob()
    new, report = save_edit.give_items(raw, backend, [save_edit.Gift(1, 100)], now=NOW)
    values, elements = _bag(backend, new)
    copper = values[-1]
    assert copper["_itemKey"] == 1 and copper["_stackCount"] == 100
    assert copper["_slotNo"] == 2                                   # lowest free slot
    assert copper["_itemNo"] == save_edit.ITEM_NO_BASE + 1
    data = new[elements[-1].start_offset:elements[-1].end_offset]
    assert struct.pack("<I", 1) in data[-30:]                       # transferred item key follows the new key
    assert struct.pack("<Q", NOW) in data and data[-5] == 1         # received now, marked new
    assert backend.decode(new).items == {50001: 14, 75001: 3, 1: 100}
    assert backend.decode(new).knowledge == backend.decode(raw).knowledge


def test_give_raises_an_existing_stack_in_place(backend):
    raw = make_inventory_blob()
    new, report = save_edit.give_items(raw, backend, [save_edit.Gift(75001, 2, 10)], now=NOW)
    assert len(new) == len(raw)
    assert backend.decode(new).items[75001] == 5
    assert report.log == ["item 75001: stack 3 -> 5"]


def test_give_refuses_to_overfill_a_stack(backend):
    raw = make_inventory_blob()
    with pytest.raises(save_edit.SaveEditError, match="stack is full"):
        save_edit.give_items(raw, backend, [save_edit.Gift(75001, 8, 10)], now=NOW)


def test_give_twice_numbers_items_and_slots_onward(backend):
    raw = make_inventory_blob()
    once, _ = save_edit.give_items(raw, backend, [save_edit.Gift(1, 100), save_edit.Gift(103, 1)], now=NOW)
    twice, _ = save_edit.give_items(once, backend, [save_edit.Gift(720004, 5), save_edit.Gift(1, 50)], now=NOW)
    values, _ = _bag(backend, twice)
    by_key = {v["_itemKey"]: v for v in values}
    assert by_key[1]["_stackCount"] == 150
    assert [by_key[k]["_slotNo"] for k in (1, 103, 720004)] == [2, 3, 4]
    assert [by_key[k]["_itemNo"] - save_edit.ITEM_NO_BASE for k in (1, 103, 720004)] == [1, 2, 3]
    res = backend.parse(twice)
    save_edit.pointer_sites(twice, res["objects"])                   # every pointer still points at itself
    st = parc.parse_structure(twice)
    assert st.stream_size - len(twice) == parc.parse_structure(raw).stream_size - len(raw)


def test_learn_and_give_combine(backend):
    raw = make_inventory_blob()
    new, _ = save_edit.learn_knowledge(raw, backend, [40002])
    new, _ = save_edit.give_items(new, backend, [save_edit.Gift(1, 100)], now=NOW)
    state = backend.decode(new)
    assert 40002 in state.knowledge and state.items[1] == 100
