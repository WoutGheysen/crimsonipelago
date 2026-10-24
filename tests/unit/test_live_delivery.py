"""Live delivery in the delivery engine: items given in the running game, settled when the game saves."""

import time

import pytest
from crimson_desert.catalogue import ItemDef

from crimsonipelago.client import events as ev
from crimsonipelago.client.database import DeliveryState
from crimsonipelago.client.item_delivery import DeliveryEngine
from crimsonipelago.game_adapter.runtime.live import LiveResult


def _item(ap_id, name, item_key, count):
    return ItemDef(ap_id, name, "filler", "resource", {"kind": "inventory_item", "item_key": item_key, "count": count},
                   1, True, True, "supported", True, "test", ())


class FakeLive:
    learns_knowledge = False

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []
        self.is_ready = True

    def give_knowledge(self, key):
        self.calls.append(("knowledge", key))
        return self.results.pop(0) if self.results else LiveResult("given")

    def ready(self):
        return self.is_ready

    def poll(self):
        self.polls = getattr(self, "polls", 0) + 1

    def give(self, key, count):
        self.calls.append((key, count))
        return self.results.pop(0) if self.results else LiveResult("given")


@pytest.fixture
def setup(ledger, sim, catalogue, bus):
    copper, ore = _item(9101, "100 Copper", 1, 100), _item(9102, "3 Ore", 721506, 3)
    for item in (copper, ore):
        catalogue.items[item.name] = item
        catalogue._by_item_id[item.ap_id] = item
    session = ledger.get_or_create_session("SEED", 0, 1, "Wout", None, catalogue.fingerprint, "0.1.0")
    live = FakeLive()
    engine = DeliveryEngine(ledger, sim, catalogue, bus, live=live)
    events = []
    bus.subscribe(events.append)
    return session.id, engine, live, events


def receive(ledger, sid, catalogue, *names):
    idx = ledger.next_index(sid)
    for offset, name in enumerate(names):
        ledger.record_received(sid, idx + offset, catalogue.items[name].ap_id, 100 + offset, 2, 1)


def states(ledger, sid):
    return [r.state for r in ledger.items(sid)]


def test_items_arrive_in_the_running_game_and_flight_waits(setup, ledger, sim, catalogue):
    sid, engine, live, events = setup
    sim.update(game_running=True, saved_at=0)
    receive(ledger, sid, catalogue, "100 Copper", "Flight", "3 Ore")
    engine.validate(sid)
    report = engine.deliver_pending(sid)
    assert live.calls == [(1, 100), (721506, 3)]
    assert states(ledger, sid) == [DeliveryState.IN_GAME, DeliveryState.PENDING, DeliveryState.IN_GAME]
    assert report.delivered == 2 and report.pending == 1 and "running" in report.waiting_reason
    assert any(e.kind == ev.REWARD_DELIVERED and "Delivered in the game: 100 Copper, 3 Ore" in e.message
               for e in events)
    assert sim.get_inventory_snapshot() == {}                    # the save wasn't touched


def test_complete_once_the_game_saves(setup, ledger, sim, catalogue):
    sid, engine, live, events = setup
    sim.update(game_running=True, saved_at=0)
    receive(ledger, sid, catalogue, "100 Copper")
    engine.validate(sid)
    engine.deliver_pending(sid)
    engine.deliver_pending(sid)
    assert states(ledger, sid) == [DeliveryState.IN_GAME]          # no save yet
    sim.update(saved_at=time.time() + 5)
    engine.deliver_pending(sid)
    assert states(ledger, sid) == [DeliveryState.COMPLETE] and len(live.calls) == 1


def test_given_again_when_the_game_closes_without_saving(setup, ledger, sim, catalogue):
    sid, engine, live, events = setup
    sim.update(game_running=True, saved_at=0)
    receive(ledger, sid, catalogue, "100 Copper")
    engine.validate(sid)
    engine.deliver_pending(sid)
    sim.update(game_running=False)
    live.is_ready = False                                        # the plugin went with the game
    report = engine.deliver_pending(sid)
    assert states(ledger, sid) == [DeliveryState.COMPLETE] and report.delivered == 1   # through the save
    assert sim.get_inventory_snapshot() == {"100 Copper": 1}
    assert any("closed without saving" in e.message for e in events)


def test_refused_items_wait_for_the_save(setup, ledger, sim, catalogue):
    sid, engine, live, events = setup
    live.results = [LiveResult("refused", "the game refused it (code 0x5)", 5)]
    sim.update(game_running=True, saved_at=0)
    receive(ledger, sid, catalogue, "3 Ore")
    engine.validate(sid)
    engine.deliver_pending(sid)
    rec = ledger.items(sid)[0]
    assert rec.state is DeliveryState.PENDING and "refused" in rec.last_error and rec.attempts == 1
    sim.update(game_running=False)
    live.is_ready = False
    assert engine.deliver_pending(sid).delivered == 1


def test_waiting_for_the_game_says_why(setup, ledger, sim, catalogue):
    sid, engine, live, events = setup
    live.results = [LiveResult("not now", "waiting for the game (it may be paused, in a menu or loading)")]
    sim.update(game_running=True, saved_at=0)
    receive(ledger, sid, catalogue, "100 Copper", "3 Ore")
    engine.validate(sid)
    report = engine.deliver_pending(sid)
    assert report.pending == 2 and "waiting for the game" in report.waiting_reason
    assert live.calls == [(1, 100)]                               # stopped at the first "not now"
    assert states(ledger, sid) == [DeliveryState.PENDING] * 2


def test_without_the_plugin_nothing_changes(setup, ledger, sim, catalogue):
    sid, engine, live, events = setup
    live.is_ready = False
    sim.update(game_running=True)
    receive(ledger, sid, catalogue, "100 Copper")
    engine.validate(sid)
    report = engine.deliver_pending(sid)
    assert live.calls == [] and "plugin isn't connected" in report.waiting_reason
    assert live.polls == 1                                       # the channel still gets its tick


def test_flight_goes_in_live_once_enabled_for_the_build(setup, ledger, sim, catalogue):
    sid, engine, live, events = setup
    live.learns_knowledge = True
    sim.update(game_running=True, saved_at=0)
    receive(ledger, sid, catalogue, "Flight")
    engine.validate(sid)
    report = engine.deliver_pending(sid)
    assert live.calls == [("knowledge", 40002)] and report.delivered == 1
    assert states(ledger, sid) == [DeliveryState.IN_GAME]


def test_flight_refused_is_tried_again_then_left_for_the_save(setup, ledger, sim, catalogue):
    sid, engine, live, events = setup
    live.learns_knowledge = True
    live.results = [LiveResult("refused", "the game refused it (code 0x78c0a4e9)", 0x78C0A4E9)] * 3
    sim.update(game_running=True, saved_at=0)
    receive(ledger, sid, catalogue, "Flight")
    engine.validate(sid)
    for _ in range(4):
        engine.deliver_pending(sid)
    assert live.calls == [("knowledge", 40002)] * 3                 # three tries, then no more
    rec = ledger.items(sid)[0]
    assert rec.state is DeliveryState.PENDING and "quit to desktop" in rec.last_error
    sim.update(game_running=False)
    live.is_ready = False
    assert engine.deliver_pending(sid).delivered == 1                 # through the save


def test_flight_learned_on_the_second_try(setup, ledger, sim, catalogue):
    sid, engine, live, events = setup
    live.learns_knowledge = True
    live.results = [LiveResult("refused", "the game refused it", 0x78C0A4E9), LiveResult("given")]
    sim.update(game_running=True, saved_at=0)
    receive(ledger, sid, catalogue, "Flight")
    engine.validate(sid)
    engine.deliver_pending(sid)
    assert "trying again" in ledger.items(sid)[0].last_error
    engine.deliver_pending(sid)
    assert states(ledger, sid) == [DeliveryState.IN_GAME]


class WatchingLive(FakeLive):
    """FakeLive that notices loads, as LiveChannel does."""

    def __init__(self, *results):
        super().__init__(*results)
        self.loads, self.load_at, self.last_seen_unix = 0, 0.0, None

    def load(self, at=None):
        self.loads += 1
        self.load_at = time.time() + 1 if at is None else at


@pytest.fixture
def watching(ledger, sim, catalogue, bus, setup):
    sid, _engine, _live, events = setup
    live = WatchingLive()
    return sid, DeliveryEngine(ledger, sim, catalogue, bus, live=live), live, events


def test_a_save_loaded_before_the_game_saved_gives_them_again(watching, ledger, sim, catalogue):
    sid, engine, live, events = watching
    sim.update(game_running=True, saved_at=0)
    receive(ledger, sid, catalogue, "100 Copper")
    engine.validate(sid)
    engine.deliver_pending(sid)
    assert states(ledger, sid) == [DeliveryState.IN_GAME]
    live.load()
    engine.deliver_pending(sid)
    assert live.calls == [(1, 100), (1, 100)]                        # given again, into the loaded game
    assert states(ledger, sid) == [DeliveryState.IN_GAME]
    assert any("A save was loaded before the game saved 100 Copper" in e.message for e in events)
    engine.deliver_pending(sid)
    assert len(live.calls) == 2                                       # once per load


def test_an_autosave_after_a_load_does_not_count(watching, ledger, sim, catalogue):
    sid, engine, live, events = watching
    sim.update(game_running=True, saved_at=0)
    receive(ledger, sid, catalogue, "100 Copper")
    engine.validate(sid)
    engine.deliver_pending(sid)
    live.last_seen_unix = int(time.time()) - 1                       # the game was last seen before the save
    sim.update(saved_at=time.time() + 5)                              # an autosave, just after a load
    engine.deliver_pending(sid)
    assert states(ledger, sid) == [DeliveryState.IN_GAME]            # not complete: not seen since that save
    live.load()                                                       # the next sighting shows the load
    engine.deliver_pending(sid)
    assert len(live.calls) == 2 and states(ledger, sid) == [DeliveryState.IN_GAME]


def test_complete_once_the_game_is_seen_after_its_save(watching, ledger, sim, catalogue):
    sid, engine, live, events = watching
    sim.update(game_running=True, saved_at=0)
    receive(ledger, sid, catalogue, "100 Copper")
    engine.validate(sid)
    engine.deliver_pending(sid)
    saved = time.time() + 5
    sim.update(saved_at=saved)
    live.last_seen_unix = int(saved) + 2                              # the game went on after the save
    engine.deliver_pending(sid)
    assert states(ledger, sid) == [DeliveryState.COMPLETE]
    live.load()                                                       # loading later doesn't take it back
    engine.deliver_pending(sid)
    assert states(ledger, sid) == [DeliveryState.COMPLETE] and len(live.calls) == 1


def test_saved_then_closed_is_complete_without_seeing_the_game_again(watching, ledger, sim, catalogue):
    sid, engine, live, events = watching
    sim.update(game_running=True, saved_at=0)
    receive(ledger, sid, catalogue, "100 Copper")
    engine.validate(sid)
    engine.deliver_pending(sid)
    sim.update(saved_at=time.time() + 5, game_running=False)          # save and quit
    live.is_ready = False
    engine.deliver_pending(sid)
    assert states(ledger, sid) == [DeliveryState.COMPLETE] and len(live.calls) == 1
    assert sim.get_inventory_snapshot() == {}                         # not written to the save a second time


def test_items_given_after_the_load_was_noticed_stay(watching, ledger, sim, catalogue):
    sid, engine, live, events = watching
    sim.update(game_running=True, saved_at=0)
    live.load(at=time.time() - 60)                                    # an earlier load, already noticed
    engine.deliver_pending(sid)
    receive(ledger, sid, catalogue, "100 Copper")
    engine.validate(sid)
    engine.deliver_pending(sid)
    engine.notice_loads(sid)                                          # no new load: nothing changes
    assert states(ledger, sid) == [DeliveryState.IN_GAME] and len(live.calls) == 1


def test_the_live_reason_is_shown_next_to_the_save_reason(setup, ledger, sim, catalogue):
    sid, engine, live, events = setup
    live.results = [LiveResult("not now", "waiting for the game (it pauses while in the background)")]
    sim.update(game_running=True, saved_at=0)
    receive(ledger, sid, catalogue, "100 Copper", "Traversal Unlock B")
    engine.validate(sid)
    report = engine.deliver_pending(sid)
    assert "running" in report.waiting_reason and "1 of them go in while you play" in report.waiting_reason
    assert "pauses while in the background" in report.waiting_reason
    live.is_ready = False
    report = engine.deliver_pending(sid)
    assert "the game plugin isn't connected" in report.waiting_reason


def test_a_refusal_is_shown(setup, ledger, sim, catalogue):
    sid, engine, live, events = setup
    live.results = [LiveResult("refused", "the game refused it (code 0x7)", 7)]
    sim.update(game_running=True, saved_at=0)
    receive(ledger, sid, catalogue, "3 Ore")
    engine.validate(sid)
    engine.deliver_pending(sid)
    assert any("3 Ore: the game refused it (code 0x7)" in e.message and "quit to desktop" in e.message for e in events)
