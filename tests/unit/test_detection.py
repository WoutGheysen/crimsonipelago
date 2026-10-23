"""Check detection from decoded save state, and which items need a save write."""

from crimsonipelago.game_adapter.crimson_adapter import CrimsonDesertSaveAdapter
from crimsonipelago.game_adapter.nattkh_backend import DeepState


def adapter(cat, tmp_path):
    return CrimsonDesertSaveAdapter(cat, save_path=tmp_path / "none.save", backup_dir=tmp_path / "b",
                                    campaigns_file=tmp_path / "c.json")


def test_objective_counts_once_its_mission_completes(real_catalogue, tmp_path):
    a = adapter(real_catalogue, tmp_path)
    loc = real_catalogue.locations["Where Rumors Gather: Try arm wrestling"]
    det = dict(loc.detection)
    state = DeepState()
    state.missions[det["mission_key"]] = {"state": 2}
    assert not a._detected(det, state)
    state.missions[det["parent_mission_key"]] = {"state": 5}      # the player skipped it, the mission is done
    assert a._detected(det, state)
    state.missions[det["parent_mission_key"]] = {"state": 2}
    state.missions[det["mission_key"]] = {"state": 5}
    assert a._detected(det, state)


def test_filler_needs_no_write_but_flight_does(real_catalogue, tmp_path):
    a = adapter(real_catalogue, tmp_path)
    assert not a.needs_write(real_catalogue.items["Crow Feather"])
    assert a.can_grant_item(real_catalogue.items["Crow Feather"]).ok
    assert a.needs_write(real_catalogue.items["Flight"])


def test_without_the_decoder_nothing_is_bound(real_catalogue, tmp_path):
    from tests.fixtures.synthetic_save import make_save

    save = tmp_path / "123" / "slot107" / "save.save"
    save.parent.mkdir(parents=True)
    save.write_bytes(make_save())
    a = CrimsonDesertSaveAdapter(real_catalogue, save_path=save, editor_dir=tmp_path / "not-the-editor",
                                 backup_dir=tmp_path / "b", campaigns_file=tmp_path / "c.json")
    assert a.get_save_identity() is None           # never fall back to the slot: the game rotates them
    assert "--editor-dir" in a.identity_problem
    assert "Save editor folder not usable" in a.setup_warnings()[0]


def test_only_a_save_written_after_the_seed_counts(real_catalogue, tmp_path):
    import os

    from tests.fixtures.synthetic_save import make_save

    save = tmp_path / "save.save"
    save.write_bytes(make_save())
    os.utime(save, (1_000_000, 1_000_000))
    a = CrimsonDesertSaveAdapter(real_catalogue, save_path=save, backup_dir=tmp_path / "b",
                                 campaigns_file=tmp_path / "c.json")
    assert not a.save_written_after(2_000_000)       # an old playthrough is never picked up by a new seed
    os.utime(save, (3_000_000, 3_000_000))
    assert a.save_written_after(2_000_000)
