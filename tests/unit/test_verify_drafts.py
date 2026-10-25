"""Confirming drafts from a playthrough's live logs."""

import json
import shutil

from crimson_desert import catalogue as cat_mod

from crimsonipelago.tools import verify_drafts
from crimsonipelago.tools.draft_slices import DATA


def _log(cat, goal="chapter_2"):
    rows, t = [], 0
    jump_at = None
    for loc in sorted(cat.locations_in_scope(goal), key=lambda x: x.ap_id):
        if loc.verified or loc.no_progression or cat.region_slices.get(loc.region, 1) != 2:
            continue
        t += 1
        rows.append({"at": f"2026-10-26T10:{t // 60:02d}:{t % 60:02d}.000+01:00", "event": "draft_check",
                     "ap_id": loc.ap_id, "name": loc.name, "region": loc.region, "_log": "live-20261026.jsonl"})
        if t == 5:
            jump_at = rows[-1]["at"].replace(".000", ".500")
            rows.append({"at": jump_at, "event": "give", "what": "knowledge", "key": 1001116, "state": "given"})
    rows.append({"at": "2026-10-26T12:00:00.000+01:00", "event": "draft_goal", "goal": goal})
    return rows


def test_a_finished_chapter_in_the_logs_confirms_its_checks_and_goal(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(DATA, data)
    cat = cat_mod.load(data)
    result = verify_drafts.analyse(sorted(_log(cat), key=lambda r: r["at"]), cat)
    assert result["goals"]["chapter_2"]["unseen"] == []
    assert "Double Jump" in result["items"]
    # checks done before Double Jump arrived, in regions logic gates on it
    assert all("without Double Jump" in line or "without Flight" in line for line in result["early"])
    assert any("without Flight" in line for line in result["early"])          # Flight never came in this log
    lines = verify_drafts.report(result, cat)
    assert any(line.strip().startswith("2:") for line in lines)

    marked = verify_drafts.write(result, data)
    after = cat_mod.load(data)                                                # validates
    assert marked["goals"] == 1 and after.goals["chapter_2"].verified
    assert all(loc.verified for loc in after.locations_in_scope("chapter_2") if not loc.no_progression)
    assert not after.goals["chapter_3"].verified
    # the new catalogue's fingerprints are recorded, and seeds made before it still play
    seeds = json.loads((data / "seed_fingerprints.json").read_text())["seeds"]
    for goal in after.goals:
        assert after.scope_fingerprint(goal) in seeds
        old = after.compare_seed(cat.scope_fingerprint(goal), goal)
        assert old.playable and not old.changed

def test_nothing_seen_marks_nothing(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(DATA, data)
    cat = cat_mod.load(data)
    result = verify_drafts.analyse([], cat)
    assert verify_drafts.write(result, data) == {"checks": 0, "goals": 0}
    assert "none yet" in "\n".join(verify_drafts.report(result, cat))


def test_log_arguments_may_be_a_folder_or_a_pattern(tmp_path):
    for name in ("live-20261026.jsonl", "live-20261027.jsonl", "other.txt"):
        (tmp_path / name).write_text("")
    assert [p.name for p in verify_drafts.expand([tmp_path])] == ["live-20261026.jsonl", "live-20261027.jsonl"]
    assert len(verify_drafts.expand([tmp_path / "live-*.jsonl"])) == 2
