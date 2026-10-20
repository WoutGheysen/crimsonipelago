import pytest

from crimsonipelago.game_adapter import parc
from tests.fixtures.synthetic_save import TYPES, make_blob


def test_parse_structure_of_synthetic_blob():
    s = parc.parse_structure(make_blob())
    assert s.root_type == "SaveRoot"
    assert s.type_names() == [t[0] for t in TYPES]
    assert [e.class_name for e in s.toc] == ["SaveRoot", "QuestSaveData", "KnowledgeSaveData", "InventorySaveData"]
    assert s.warnings == []


def test_object_digests_change_only_for_changed_object():
    a, b = make_blob(quest_state=3), make_blob(quest_state=5)
    da = parc.object_digests(a, parc.parse_structure(a))
    db = parc.object_digests(b, parc.parse_structure(b))
    changed = [x["class"] for x, y in zip(da, db) if x["sha256"] != y["sha256"]]
    assert changed == ["QuestSaveData"]


def test_rejects_non_serializer_blob():
    with pytest.raises(parc.ParcFormatError):
        parc.parse_structure(b"\x00" * 100)


def test_truncated_blob_is_an_error_not_a_crash():
    blob = make_blob()
    with pytest.raises(parc.ParcFormatError):
        parc.parse_structure(blob[:40])
