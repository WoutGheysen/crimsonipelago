"""rebind: continue a seed on another playthrough when the player lost theirs."""

import argparse

from crimsonipelago.client.database import DeliveryState, Ledger
from crimsonipelago.client.main import cmd_rebind
from crimsonipelago.game_adapter.sim_adapter import SimulatedGameAdapter


def _args(tmp_path, yes=False, give_again="ask"):
    return argparse.Namespace(adapter="sim", sim_file=str(tmp_path / "sim.json"), save_path=None, editor_dir=None,
                              steam_manifest=None, yes=yes, give_again=give_again)


def _seed(tmp_path, catalogue, profile="sim/old", other_seed_on_new=False):
    SimulatedGameAdapter(tmp_path / "sim.json", profile_id="sim/new")
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    if other_seed_on_new:
        other = ledger.get_or_create_session("OTHER", 0, 1, "Wout", None, catalogue.fingerprint, "0.1.0")
        ledger.bind_profile(other.id, "sim/new")
    session = ledger.get_or_create_session("SEED", 0, 1, "Wout", None, catalogue.fingerprint, "0.1.0")
    ledger.bind_profile(session.id, profile)
    items = list(catalogue.items.values())[3:6]
    for n, item in enumerate(items):
        ledger.record_received(session.id, n, item.ap_id, 100 + n, 2, 1)
    ledger.set_item_state(session.id, [0, 1], DeliveryState.COMPLETE)
    ledger.set_item_state(session.id, [2], DeliveryState.PENDING, bump_attempts=True)
    return ledger, session


def test_rebind_moves_the_seed_and_gives_its_items_again(tmp_path, catalogue):
    ledger, session = _seed(tmp_path, catalogue)
    ledger.close()
    said = []
    assert cmd_rebind(_args(tmp_path), tmp_path, catalogue, ask=lambda p: (said.append(p), "y")[1]) == 0
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    try:
        assert ledger.session(session.id).profile_id == "sim/new"
        items = ledger.items(session.id)
        assert [r.state for r in items] == [DeliveryState.PENDING] * 3
        assert [r.attempts for r in items[:2]] == [0, 0] and "moved to playthrough" in items[0].last_error
    finally:
        ledger.close()


def test_rebind_asks_first(tmp_path, catalogue):
    ledger, session = _seed(tmp_path, catalogue)
    ledger.close()
    assert cmd_rebind(_args(tmp_path), tmp_path, catalogue, ask=lambda p: "n") == 1
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    try:
        assert ledger.session(session.id).profile_id == "sim/old"
    finally:
        ledger.close()


def test_rebind_refuses_a_playthrough_of_another_seed(tmp_path, catalogue):
    ledger, session = _seed(tmp_path, catalogue, other_seed_on_new=True)
    assert ledger.latest_session().id == session.id
    ledger.close()
    assert cmd_rebind(_args(tmp_path, yes=True), tmp_path, catalogue) == 1


def test_rebind_back_to_a_playthrough_that_has_the_items_gives_nothing_again(tmp_path, catalogue):
    ledger, session = _seed(tmp_path, catalogue)
    ledger.close()
    answers = iter(["y", "n"])
    assert cmd_rebind(_args(tmp_path), tmp_path, catalogue, ask=lambda p: next(answers)) == 0
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    try:
        assert ledger.session(session.id).profile_id == "sim/new"
        assert [r.state for r in ledger.items(session.id)] == [DeliveryState.COMPLETE] * 2 + [DeliveryState.PENDING]
    finally:
        ledger.close()
