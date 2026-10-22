"""Delivery state machine, including the fault-injection cases of scope doc 10.4."""

import pytest

from crimsonipelago.client import events as ev
from crimsonipelago.client.database import BatchState, DeliveryState
from crimsonipelago.client.item_delivery import DeliveryEngine
from crimsonipelago.game_adapter.sim_adapter import SimulatedCrash, SimulatedGameAdapter


@pytest.fixture
def setup(ledger, sim, catalogue, bus):
    session = ledger.get_or_create_session("SEED", 0, 1, "Wout", None, catalogue.fingerprint, "0.1.0")
    engine = DeliveryEngine(ledger, sim, catalogue, bus)
    return session.id, engine


def receive(ledger, sid, catalogue, *names, start=None):
    idx = ledger.next_index(sid) if start is None else start
    for offset, name in enumerate(names):
        ledger.record_received(sid, idx + offset, catalogue.items[name].ap_id, 100 + offset, 2, 1)


def states(ledger, sid):
    return [r.state for r in ledger.items(sid)]


def test_normal_delivery(setup, ledger, sim, catalogue):
    sid, engine = setup
    receive(ledger, sid, catalogue, "Key Item", "Resource Bundle", "Resource Bundle")
    engine.validate(sid)
    assert states(ledger, sid) == [DeliveryState.PENDING] * 3
    report = engine.deliver_pending(sid)
    assert report.delivered == 3
    assert states(ledger, sid) == [DeliveryState.COMPLETE] * 3
    assert sim.get_inventory_snapshot() == {"Key Item": 1, "Resource Bundle": 2}


def test_waits_while_game_running(setup, ledger, sim, catalogue):
    sid, engine = setup
    receive(ledger, sid, catalogue, "Key Item")
    sim.update(game_running=True)
    engine.validate(sid)
    report = engine.deliver_pending(sid)
    assert report.delivered == 0 and "running" in report.waiting_reason
    assert sim.get_inventory_snapshot() == {}
    sim.update(game_running=False)
    assert engine.deliver_pending(sid).delivered == 1


def test_reconnect_replay_does_not_duplicate(setup, ledger, sim, catalogue):
    sid, engine = setup
    receive(ledger, sid, catalogue, "Key Item")
    engine.validate(sid)
    engine.deliver_pending(sid)
    receive(ledger, sid, catalogue, "Key Item", start=0)  # server resends index 0 after reconnect
    engine.validate(sid)
    engine.deliver_pending(sid)
    assert sim.get_inventory_snapshot() == {"Key Item": 1}


def test_crash_after_commit_is_recovered_without_duplicate(setup, ledger, sim, catalogue, bus):
    sid, engine = setup
    receive(ledger, sid, catalogue, "Key Item")
    engine.validate(sid)
    sim.crash_after_commit = True
    with pytest.raises(SimulatedCrash):
        engine.deliver_pending(sid)
    assert states(ledger, sid) == [DeliveryState.APPLYING]

    # "restart": a fresh engine and adapter over the same files
    fresh = SimulatedGameAdapter(sim.path)
    engine2 = DeliveryEngine(ledger, fresh, catalogue, bus)
    engine2.recover(sid)
    assert states(ledger, sid) == [DeliveryState.COMPLETE]
    engine2.deliver_pending(sid)
    assert fresh.get_inventory_snapshot() == {"Key Item": 1}


def test_crash_before_commit_retries_once(setup, ledger, sim, catalogue, bus):
    sid, engine = setup
    receive(ledger, sid, catalogue, "Key Item")
    engine.validate(sid)
    sim.crash_before_commit = True
    with pytest.raises(SimulatedCrash):
        engine.deliver_pending(sid)
    fresh = SimulatedGameAdapter(sim.path)
    engine2 = DeliveryEngine(ledger, fresh, catalogue, bus)
    engine2.recover(sid)
    assert states(ledger, sid) == [DeliveryState.PENDING]
    assert engine2.deliver_pending(sid).delivered == 1
    assert fresh.get_inventory_snapshot() == {"Key Item": 1}
    # the abandoned temp file was cleaned up
    assert not list(sim.path.parent.glob("*.pending-*"))


def test_save_changed_after_crash_requires_user_decision(setup, ledger, sim, catalogue, bus):
    sid, engine = setup
    receive(ledger, sid, catalogue, "Key Item")
    engine.validate(sid)
    sim.crash_after_commit = True
    with pytest.raises(SimulatedCrash):
        engine.deliver_pending(sid)
    fresh = SimulatedGameAdapter(sim.path)
    fresh.complete_location("Prologue Quest 1")  # the player kept playing before the client restarted
    engine2 = DeliveryEngine(ledger, fresh, catalogue, bus)
    engine2.recover(sid)
    assert states(ledger, sid) == [DeliveryState.RECOVERY_REQUIRED]
    assert any(e.kind == ev.RECOVERY_REQUIRED for e in bus.log)

    # new rewards are held back while a delivery is unresolved
    receive(ledger, sid, catalogue, "Coin Pouch")
    engine2.validate(sid)
    assert engine2.deliver_pending(sid).delivered == 0

    batch = ledger.batches(sid, (BatchState.RECOVERY,))[0]
    engine2.resolve_recovery(sid, batch.batch_id, "delivered")
    assert engine2.deliver_pending(sid).delivered == 1
    assert fresh.get_inventory_snapshot() == {"Key Item": 1, "Coin Pouch": 1}


def test_recovery_retry_path(setup, ledger, sim, catalogue, bus):
    sid, engine = setup
    receive(ledger, sid, catalogue, "Key Item")
    engine.validate(sid)
    sim.crash_after_commit = True
    with pytest.raises(SimulatedCrash):
        engine.deliver_pending(sid)
    # user restores their backup from before the delivery, then edits something else
    state = sim.read_state()
    state["inventory"] = {}
    state["completed_locations"] = ["Prologue Quest 2"]
    sim.write_state(state)
    fresh = SimulatedGameAdapter(sim.path)
    engine2 = DeliveryEngine(ledger, fresh, catalogue, bus)
    engine2.recover(sid)
    batch = ledger.batches(sid, (BatchState.RECOVERY,))[0]
    engine2.resolve_recovery(sid, batch.batch_id, "retry")
    assert engine2.deliver_pending(sid).delivered == 1
    assert fresh.get_inventory_snapshot() == {"Key Item": 1}


def test_commit_failure_keeps_item_pending(setup, ledger, sim, catalogue):
    sid, engine = setup
    receive(ledger, sid, catalogue, "Key Item")
    engine.validate(sid)
    sim.fail_commit = "disk full"
    report = engine.deliver_pending(sid)
    assert report.delivered == 0 and report.waiting_reason == "disk full"
    assert states(ledger, sid) == [DeliveryState.PENDING]
    assert ledger.batches(sid, (BatchState.ABANDONED,))
    sim.fail_commit = None
    assert engine.deliver_pending(sid).delivered == 1
    assert sim.get_inventory_snapshot() == {"Key Item": 1}


def test_save_unavailable(setup, ledger, sim, catalogue):
    sid, engine = setup
    receive(ledger, sid, catalogue, "Key Item")
    engine.validate(sid)
    sim.path.unlink()
    report = engine.deliver_pending(sid)
    assert report.delivered == 0 and "missing" in report.waiting_reason
    assert states(ledger, sid) == [DeliveryState.PENDING]


def test_unsupported_items_are_parked_not_lost(setup, ledger, sim, catalogue):
    sid, engine = setup
    sim.ungrantable = {"Ambush Trap"}
    receive(ledger, sid, catalogue, "Ambush Trap", "Key Item")
    engine.validate(sid)
    assert states(ledger, sid) == [DeliveryState.UNSUPPORTED, DeliveryState.PENDING]
    engine.deliver_pending(sid)
    sim.ungrantable = set()
    engine.revalidate_unsupported(sid)
    engine.validate(sid)
    engine.deliver_pending(sid)
    assert sim.get_inventory_snapshot() == {"Key Item": 1, "Ambush Trap": 1}


def test_unknown_item_id_is_parked(setup, ledger, catalogue):
    sid, engine = setup
    ledger.record_received(sid, 0, 999999, 1, 2, 0)
    engine.validate(sid)
    assert states(ledger, sid) == [DeliveryState.UNSUPPORTED]


def test_many_pending_rewards_are_batched(setup, ledger, sim, catalogue):
    sid, engine = setup
    receive(ledger, sid, catalogue, *(["Coin Pouch"] * 120))
    engine.validate(sid)
    delivered = 0
    while (r := engine.deliver_pending(sid)).delivered:
        delivered += r.delivered
    assert delivered == 120
    assert sim.get_inventory_snapshot() == {"Coin Pouch": 120}
    assert len(ledger.batches(sid, (BatchState.COMMITTED,))) == 3


def test_unlock_lost_by_loading_an_older_save_is_given_again(setup, ledger, sim, catalogue, bus):
    sid, engine = setup
    receive(ledger, sid, catalogue, "Flight", "Resource Bundle")
    engine.validate(sid)
    engine.deliver_pending(sid)
    assert sim.get_inventory_snapshot() == {"Flight": 1, "Resource Bundle": 1}
    assert engine.reconcile(sid) == 0
    sim.update(inventory={})                      # the player loaded a save from before the delivery
    assert engine.reconcile(sid) == 1             # Flight comes back; the consumable is not duplicated
    assert states(ledger, sid) == [DeliveryState.PENDING, DeliveryState.COMPLETE]
    assert any("missing Flight" in e.message for e in bus.log)
    engine.deliver_pending(sid)
    assert sim.get_inventory_snapshot() == {"Flight": 1}
    assert engine.reconcile(sid) == 0


class NoWriteSim(SimulatedGameAdapter):
    def needs_write(self, item):
        return item.name != "Resource Bundle"


def test_items_without_an_effect_complete_while_the_game_runs(tmp_path, ledger, catalogue, bus):
    sim = NoWriteSim(tmp_path / "sim.json")
    sid = ledger.get_or_create_session("SEED", 0, 1, "Wout", None, catalogue.fingerprint, "0.1.0").id
    engine = DeliveryEngine(ledger, sim, catalogue, bus)
    receive(ledger, sid, catalogue, "Resource Bundle", "Key Item")
    sim.update(game_running=True)
    engine.validate(sid)
    report = engine.deliver_pending(sid)
    assert states(ledger, sid) == [DeliveryState.COMPLETE, DeliveryState.PENDING]
    assert report.pending == 1 and "running" in report.waiting_reason
    assert sim.get_inventory_snapshot() == {}               # nothing was written for the filler
    sim.update(game_running=False)
    engine.deliver_pending(sid)
    assert states(ledger, sid) == [DeliveryState.COMPLETE, DeliveryState.COMPLETE]
    assert sim.get_inventory_snapshot() == {"Key Item": 1}
