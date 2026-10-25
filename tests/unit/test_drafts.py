"""Draft slices in the client: reported and delivered only for seeds that include them, and logged when seen."""

import copy

from crimson_desert import catalogue as cat_mod

from crimsonipelago.client import events as ev
from crimsonipelago.client.check_tracker import CheckTracker
from crimsonipelago.game_adapter.crimson_adapter import CrimsonDesertSaveAdapter


def test_the_tracker_logs_draft_checks_and_draft_goals(ledger, sim, real_catalogue, bus):
    cat = real_catalogue
    session = ledger.get_or_create_session("SEED", 0, 1, "Wout", None, cat.fingerprint, "0.1.0")
    verified = next(loc for loc in cat.locations.values() if loc.verified)
    draft = next(loc for loc in cat.locations.values() if not loc.verified)
    sim.complete_location(verified.name)
    sim.complete_location(draft.name)
    logged = []
    tracker = CheckTracker(ledger, sim, cat, bus, log=logged.append)
    assert tracker.poll(session.id) == {verified.ap_id, draft.ap_id}
    assert [r["name"] for r in logged if r["event"] == "draft_check"] == [draft.name]
    assert any("(draft: now seen in play)" in e.message for e in bus.log if e.kind == ev.CHECK_DETECTED)
    sim.update(goal_complete=True)
    tracker.poll_goal(session.id, "chapter_2")
    assert logged[-1] == {"event": "draft_goal", "goal": "chapter_2", "detection": dict(cat.goals["chapter_2"].detection)}


def test_the_save_adapter_reports_and_delivers_drafts_only_when_the_seed_has_them(tmp_path, real_catalogue):
    adapter = CrimsonDesertSaveAdapter(real_catalogue, editor_dir=None, save_path=tmp_path / "none.save",
                                       backup_dir=tmp_path / "b", campaigns_file=tmp_path / "c.json")
    double_jump = real_catalogue.items["Double Jump"]
    assert not double_jump.deliverable and double_jump.draft_deliverable
    assert "no verified delivery procedure" in adapter.can_grant_item(double_jump).message
    adapter.include_drafts = True
    # past the draft gate (without the save editor there's no way to write knowledge, which is a different reason)
    assert "no verified delivery procedure" not in adapter.can_grant_item(double_jump).message
    assert all(loc.draft_detectable for loc in real_catalogue.locations.values())


def test_draft_items_join_the_pool_only_with_drafts(real_catalogue):
    cat = real_catalogue
    assert not cat.pool_item_eligible(cat.items["Double Jump"])
    assert cat.pool_item_eligible(cat.items["Double Jump"], drafts=True)
    raw = copy.deepcopy({k: v for k, v in cat.raw.items() if not k.startswith("_")})
    raw["items"]["slice_pool"]["2"]["Vitality of the Abyss"] = 1           # an item from a later slice is fine
    raw["items"]["slice_pool"]["2"]["Nonexistent"] = 1
    problems = cat_mod.validate_catalogue(cat_mod.build(raw))
    assert any("slice_pool 2: unknown item 'Nonexistent'" in p for p in problems)
