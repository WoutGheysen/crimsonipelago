"""SyncController against an in-process fake server."""

import asyncio

import pytest

from crimsonipelago.client import events as ev
from crimsonipelago.client.database import DeliveryState
from crimsonipelago.client.synchronization import ClientConfig, SyncController
from crimsonipelago.game_adapter.sim_adapter import SimulatedGameAdapter
from tests.fixtures.fake_server import FakeAPServer


async def wait_for(predicate, timeout=5.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met in time")


@pytest.fixture
async def server(catalogue):
    srv = await FakeAPServer(slot_data={"catalogue_fingerprint": catalogue.fingerprint, "goal": "main_story",
                                        "world_version": "0.1.0", "included_check_categories": []}).start()
    yield srv
    await srv.stop()


def make_controller(tmp_path, server, sim, ledger, catalogue, bus, **cfg):
    config = ClientConfig(server=server.url, slot_name=cfg.pop("slot", "Wout"), data_dir=tmp_path / "data",
                          poll_interval=0.05, **cfg)
    return SyncController(config, sim, ledger, catalogue, bus)


async def run_until(controller, predicate, timeout=5.0):
    task = asyncio.create_task(controller.run())
    try:
        await wait_for(predicate, timeout)
    finally:
        controller.stop()
        await asyncio.wait_for(task, 5)


async def test_check_and_item_roundtrip(tmp_path, server, sim, ledger, catalogue, bus):
    c = make_controller(tmp_path, server, sim, ledger, catalogue, bus)
    sim.complete_location("Prologue Quest 1")
    await server.push_item(catalogue.items["Key Item"].ap_id)
    loc_id = catalogue.locations["Prologue Quest 1"].ap_id
    await run_until(c, lambda: loc_id in server.checked and sim.get_inventory_snapshot().get("Key Item") == 1)
    assert c.session.profile_id == "sim/slot1"
    assert any(e.kind == ev.CHECK_SENT for e in bus.log)
    assert any(e.kind == ev.REWARD_DELIVERED for e in bus.log)


async def test_offline_checks_are_sent_after_reconnect(tmp_path, server, sim, ledger, catalogue, bus):
    c = make_controller(tmp_path, server, sim, ledger, catalogue, bus)
    await run_until(c, lambda: c.authenticated)  # first connection binds the profile
    # play offline
    await server.stop()
    sim.complete_location("Prologue Quest 2")
    c2 = make_controller(tmp_path, server, sim, ledger, catalogue, bus)
    task = asyncio.create_task(c2.run())
    loc = catalogue.locations["Prologue Quest 2"].ap_id
    await wait_for(lambda: loc in ledger.unconfirmed_checks(c2.session.id) if c2.session else False)
    # server comes back on the same port
    import websockets
    server.server = await websockets.serve(server._handler, "127.0.0.1", server.port)
    await wait_for(lambda: loc in server.checked, timeout=15)
    c2.stop()
    await asyncio.wait_for(task, 5)


async def test_disconnect_does_not_duplicate_items(tmp_path, server, sim, ledger, catalogue, bus):
    c = make_controller(tmp_path, server, sim, ledger, catalogue, bus)
    await server.push_item(catalogue.items["Coin Pouch"].ap_id)
    task = asyncio.create_task(c.run())
    await wait_for(lambda: sim.get_inventory_snapshot().get("Coin Pouch") == 1)
    await server.drop_clients()  # client reconnects and receives the full list (index 0) again
    await wait_for(lambda: c.authenticated and len(server.received_cmds) > 3, timeout=10)
    await server.push_item(catalogue.items["Coin Pouch"].ap_id, location=2)
    await wait_for(lambda: sim.get_inventory_snapshot().get("Coin Pouch") == 2)
    await asyncio.sleep(0.3)
    c.stop()
    await asyncio.wait_for(task, 5)
    assert sim.get_inventory_snapshot()["Coin Pouch"] == 2


async def test_gap_triggers_sync(tmp_path, server, sim, ledger, catalogue, bus):
    c = make_controller(tmp_path, server, sim, ledger, catalogue, bus)
    task = asyncio.create_task(c.run())
    await wait_for(lambda: c.authenticated)
    # the server "skips" index 0 (e.g. we missed a packet)
    server.items.append({"item": catalogue.items["Key Item"].ap_id, "location": 1, "player": 2, "flags": 1})
    server.items.append({"item": catalogue.items["Coin Pouch"].ap_id, "location": 2, "player": 2, "flags": 0})
    await server.broadcast([{"cmd": "ReceivedItems", "index": 1, "items": [server.items[1]]}])
    await wait_for(lambda: sim.get_inventory_snapshot() == {"Key Item": 1, "Coin Pouch": 1})
    assert any(cmd["cmd"] == "Sync" for cmd in server.received_cmds)
    c.stop()
    await asyncio.wait_for(task, 5)


async def test_goal_is_reported_once(tmp_path, server, sim, ledger, catalogue, bus):
    c = make_controller(tmp_path, server, sim, ledger, catalogue, bus)
    sim.update(goal_complete=True)
    await run_until(c, lambda: 30 in server.status)
    assert server.status.count(30) == 1
    assert ledger.session(c.session.id).goal_reported


async def test_catalogue_mismatch_stops_sync(tmp_path, sim, ledger, catalogue, bus):
    srv = await FakeAPServer(slot_data={"catalogue_fingerprint": "deadbeef"}).start()
    try:
        c = make_controller(tmp_path, srv, sim, ledger, catalogue, bus)
        await asyncio.wait_for(c.run(), 5)  # returns because the connection gives up
        assert c.paused and "different Crimson Desert APWorld" in c.paused
        assert c.session is None
    finally:
        await srv.stop()


async def test_wrong_slot_name_is_fatal_with_clear_message(tmp_path, server, sim, ledger, catalogue, bus):
    c = make_controller(tmp_path, server, sim, ledger, catalogue, bus, slot="Nobody")
    await asyncio.wait_for(c.run(), 5)
    assert "No player with that slot name" in c.connection.fatal_error


async def test_wrong_profile_pauses_delivery(tmp_path, server, sim, ledger, catalogue, bus):
    c = make_controller(tmp_path, server, sim, ledger, catalogue, bus)
    await run_until(c, lambda: c.session is not None and c.session.profile_id == "sim/slot1")
    # the player loads a different save slot
    other = SimulatedGameAdapter(tmp_path / "other.json", profile_id="sim/slot2")
    await server.push_item(catalogue.items["Key Item"].ap_id)
    c2 = make_controller(tmp_path, server, other, ledger, catalogue, bus)
    await run_until(c2, lambda: c2.paused is not None and ledger.next_index(c2.session.id) == 1)
    assert "belongs to save profile sim/slot1" in c2.paused
    assert other.get_inventory_snapshot() == {}  # nothing crossed over
    assert ledger.items(c2.session.id)[0].state is DeliveryState.RECEIVED


async def test_profile_cannot_serve_two_seeds(tmp_path, catalogue, sim, ledger, bus):
    fp = {"catalogue_fingerprint": catalogue.fingerprint}
    a = await FakeAPServer(seed="SEED-A", slot_data=fp).start()
    b = await FakeAPServer(seed="SEED-B", slot_data=fp).start()
    try:
        c = make_controller(tmp_path, a, sim, ledger, catalogue, bus)
        await run_until(c, lambda: c.session is not None and c.session.profile_id is not None)
        c2 = make_controller(tmp_path, b, sim, ledger, catalogue, bus)
        await run_until(c2, lambda: c2.paused is not None)
        assert "already belongs to seed SEED-A" in c2.paused
    finally:
        await a.stop()
        await b.stop()


async def test_failing_packet_handler_reconnects_instead_of_dying(tmp_path, server, sim, ledger, catalogue, bus):
    """A bug in one packet handler must not silently end synchronization."""
    c = make_controller(tmp_path, server, sim, ledger, catalogue, bus)
    real = c._on_ReceivedItems
    calls = {"n": 0}

    async def flaky(cmd):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated handler bug")
        await real(cmd)

    c._on_ReceivedItems = flaky
    await server.push_item(catalogue.items["Key Item"].ap_id)
    await run_until(c, lambda: sim.get_inventory_snapshot().get("Key Item") == 1, timeout=10)
    assert calls["n"] >= 2, "the client reconnected and the server resent the item list"
    assert sim.get_inventory_snapshot() == {"Key Item": 1}, "delivered exactly once"
    assert any(e.kind == ev.ERROR and "simulated handler bug" in e.message for e in bus.log)
