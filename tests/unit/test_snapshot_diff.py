from crimsonipelago.game_adapter import save_diff, snapshot
from tests.fixtures.synthetic_save import make_save


def _write(tmp_path, name, **kw):
    p = tmp_path / "12345" / "slot100"
    p.mkdir(parents=True, exist_ok=True)
    f = p / name
    f.write_bytes(make_save(**kw))
    return f


def test_snapshot_has_structure_and_identity(tmp_path):
    snap = snapshot.snapshot_file(_write(tmp_path, "save.save"))
    assert snap["container"]["hmac_ok"]
    assert snap["save_identity"]["profile_id"] == "12345/slot100"
    assert [o["class"] for o in snap["structure"]["objects"]][1] == "QuestSaveData"
    assert snap["warnings"] == []


def test_diff_reports_changed_object(tmp_path):
    a = snapshot.snapshot_file(_write(tmp_path, "save.save", quest_state=3))
    b = snapshot.snapshot_file(_write(tmp_path, "save.save", quest_state=5))
    d = save_diff.diff_snapshots(a, b)
    assert d.objects_changed == ["QuestSaveData#0"]
    assert d.same_profile and not d.schema_changed
    text = save_diff.render_diff(d)
    assert "changed  QuestSaveData#0" in text
    assert "save-editor backend" in text


def test_identical_content_different_nonce(tmp_path):
    a = snapshot.snapshot_file(_write(tmp_path, "save.save"))
    b = snapshot.snapshot_file(_write(tmp_path, "save.save"))  # new random nonce -> different file
    d = save_diff.diff_snapshots(a, b)
    assert not d.same_file
    assert d.empty
    assert "differs only in encryption" in save_diff.render_diff(d)


def test_deep_state_diff_rendering():
    a = {"source": {"sha256": "a"}, "state": {"quests": {"1": {"state": 3}}, "missions": {}, "knowledge": [1],
                                              "items": {"7": 1}}}
    b = {"source": {"sha256": "b"}, "state": {"quests": {"1": {"state": 5}}, "missions": {}, "knowledge": [1, 2],
                                              "items": {"7": 3}}}
    d = save_diff.diff_snapshots(a, b)
    assert d.quests == [(1, {"state": 3}, {"state": 5})]
    assert d.knowledge_added == [2]
    assert d.items == [(7, 1, 3)]
    text = save_diff.render_diff(d)
    assert "3 (InProgress) -> 5 (Completed)" in text
    assert "(+2)" in text
