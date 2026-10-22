"""CLI tests for crimson-inspect and crimson-catalogue (read-only tools)."""

import json
import shutil
from pathlib import Path

from crimsonipelago.tools import catalogue_validator, save_inspector
from tests.fixtures.synthetic_save import make_save

DATA = Path(__file__).resolve().parents[2] / "apworld" / "crimson_desert" / "data"


def _saves(tmp_path):
    a, b = tmp_path / "a.save", tmp_path / "b.save"
    a.write_bytes(make_save(quest_state=3))
    b.write_bytes(make_save(quest_state=6))
    return a, b


def test_info_reports_structure(tmp_path, capsys):
    a, _ = _saves(tmp_path)
    assert save_inspector.main(["info", str(a)]) == 0
    out = capsys.readouterr().out
    assert "hmac OK" in out and "QuestSaveData" in out


def test_snapshot_then_diff_from_json(tmp_path, capsys):
    a, b = _saves(tmp_path)
    sa, sb = tmp_path / "a.json", tmp_path / "b.json"
    assert save_inspector.main(["snapshot", str(a), "-o", str(sa)]) == 0
    assert save_inspector.main(["snapshot", str(b), "-o", str(sb)]) == 0
    capsys.readouterr()
    assert save_inspector.main(["diff", str(sa), str(sb)]) == 0
    out = capsys.readouterr().out
    assert "changed  QuestSaveData#0" in out
    assert "KnowledgeSaveData" not in out


def test_diff_json_output(tmp_path, capsys):
    a, b = _saves(tmp_path)
    assert save_inspector.main(["diff", str(a), str(b), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["objects_changed"] == ["QuestSaveData#0"]
    assert data["same_profile"] is True


def test_identical_saves(tmp_path, capsys):
    a, _ = _saves(tmp_path)
    copy = tmp_path / "copy.save"
    shutil.copy(a, copy)
    save_inspector.main(["diff", str(a), str(copy)])
    assert "byte-identical" in capsys.readouterr().out


def test_backup_never_touches_original(tmp_path, capsys):
    a, _ = _saves(tmp_path)
    before = a.read_bytes()
    assert save_inspector.main(["backup", str(a), "--to", str(tmp_path / "bk"), "--label", "t"]) == 0
    dest = Path(capsys.readouterr().out.strip())
    assert dest.is_file() and dest.read_bytes() == before
    assert a.read_bytes() == before


def test_corrupt_save_is_a_clean_error(tmp_path, capsys):
    bad = tmp_path / "bad.save"
    bad.write_bytes(b"not a save at all" * 4)
    assert save_inspector.main(["info", str(bad)]) == 1
    assert "error:" in capsys.readouterr().err


def test_catalogue_validate_and_report(capsys):
    assert catalogue_validator.main(["validate"]) == 0
    assert "OK:" in capsys.readouterr().out
    assert catalogue_validator.main(["report"]) == 0
    out = capsys.readouterr().out
    assert "status: partial" in out and "PLACEHOLDER" not in out


def test_catalogue_ids_detect_renumbering(tmp_path, capsys):
    changed = tmp_path / "data"
    shutil.copytree(DATA, changed)
    items = json.loads((changed / "items.json").read_text(encoding="utf-8"))
    items["items"][0]["ap_id"] = int(items["items"][0]["ap_id"]) + 9999
    (changed / "items.json").write_text(json.dumps(items), encoding="utf-8")
    assert catalogue_validator.main(["--dir", str(changed), "ids", "--against", str(DATA)]) == 1
    assert "disappeared" in capsys.readouterr().out
    assert catalogue_validator.main(["ids", "--against", str(DATA)]) == 0


def test_watch_follows_every_slot_and_survives_bad_writes(tmp_path):
    root = tmp_path / "save"
    slot0 = root / "123" / "slot0"
    slot1 = root / "123" / "slot1"
    slot0.mkdir(parents=True)
    (slot0 / "save.save").write_bytes(make_save(quest_state=3))
    out, lines = tmp_path / "research", []
    w = save_inspector.Watcher([root], out, settle=0, printer=lines.append)

    assert len(w.poll()) == 1  # baseline of the existing slot
    assert w.poll() == []      # nothing changed

    # the game starts writing to a new slot: it is picked up and compared with the last snapshot
    slot1.mkdir()
    (slot1 / "save.save").write_bytes(make_save(quest_state=6))
    written = w.poll()
    assert [p.name.split("_", 1)[1] for p in written] == ["123-slot1.json"]
    assert any("first write to this file" in line for line in lines)
    assert any("QuestSaveData" in line for line in lines)

    # a half-written / corrupt save is reported and skipped, and the watcher keeps going
    import os
    (slot0 / "save.save").write_bytes(b"SAVE garbage")
    os.utime(slot0 / "save.save", (1, 1))
    assert w.poll() == []
    assert any("could not be read" in line for line in lines)
    (slot0 / "save.save").write_bytes(make_save(quest_state=6))
    os.utime(slot0 / "save.save", (2, 2))
    assert len(w.poll()) == 1
