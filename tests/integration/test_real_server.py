"""End-to-end against a real Archipelago MultiServer (scope doc 5 / 10.2).

Generates a two-game multiworld (Crimson Desert + ChecksFinder) with the
real Archipelago generator, hosts it with the real MultiServer, then plays it:
the Crimson Desert slot runs the actual SyncController on the simulated game
adapter, and the ChecksFinder slot is driven by a minimal scripted client.

Skipped unless ARCHIPELAGO_PATH points at an Archipelago checkout that has
this APWorld installed (worlds/crimson_desert or custom_worlds/crimson_desert.apworld).
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import websockets

from crimsonipelago.client import events as ev
from crimsonipelago.client.database import DeliveryState, Ledger
from crimsonipelago.client.synchronization import ClientConfig, SyncController
from crimsonipelago.game_adapter.sim_adapter import SimulatedGameAdapter
from tests.conftest import archipelago_path

AP = archipelago_path()
pytestmark = [pytest.mark.archipelago, pytest.mark.skipif(AP is None, reason="ARCHIPELAGO_PATH not set")]

CD_YAML = """name: Wout
game: Crimson Desert
Crimson Desert:
  goal: main_story
  item_pool_mode: expanded
"""
CF_YAML = """name: Partner
game: ChecksFinder
ChecksFinder: {}
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(port: int, proc: subprocess.Popen, timeout: float = 60.0) -> None:
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise RuntimeError(f"MultiServer exited early:\n{proc.stdout.read() if proc.stdout else ''}")
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise TimeoutError("MultiServer did not start listening")


@pytest.fixture(scope="module")
def multiworld(tmp_path_factory):
    work = tmp_path_factory.mktemp("ap")
    players, out = work / "players", work / "output"
    players.mkdir()
    out.mkdir()
    (players / "cd.yaml").write_text(CD_YAML, encoding="utf-8")
    (players / "cf.yaml").write_text(CF_YAML, encoding="utf-8")
    env = {**os.environ, "PYTHONWARNINGS": "ignore", "SKIP_REQUIREMENTS_UPDATE": "1"}
    gen = subprocess.run(
        [sys.executable, "Generate.py", "--player_files_path", str(players), "--outputpath", str(out),
         "--seed", "4242", "--spoiler", "1"],
        cwd=AP, env=env, capture_output=True, text=True, timeout=600)
    assert gen.returncode == 0, gen.stdout[-3000:] + gen.stderr[-3000:]
    archive = next(out.glob("*.zip"))
    with zipfile.ZipFile(archive) as z:
        z.extractall(out)
    multidata = next(out.glob("*.archipelago"))
    spoiler = next(out.glob("*_Spoiler.txt"), None)

    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "MultiServer.py", str(multidata), "--host", "127.0.0.1", "--port", str(port),
         "--disable_save", "--loglevel", "warning"],
        cwd=AP, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        _wait_port(port, proc)
        yield {"url": f"ws://127.0.0.1:{port}", "spoiler": spoiler.read_text() if spoiler else ""}
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


class ScriptedClient:
    """Just enough of the AP protocol to play the partner slot and query the server."""

    def __init__(self, url: str, name: str, game: str):
        self.url, self.name, self.game = url, name, game
        self.ws = None
        self.connected: dict = {}
        self.inbox: list[dict] = []

    async def __aenter__(self):
        self.ws = await websockets.connect(self.url, max_size=None)
        await self._expect("RoomInfo")
        await self.send({"cmd": "Connect", "game": self.game, "name": self.name, "password": "",
                         "uuid": "partner", "version": {"major": 0, "minor": 6, "build": 0, "class": "Version"},
                         "items_handling": 0b111, "tags": [], "slot_data": False})
        self.connected = await self._expect("Connected")
        return self

    async def __aexit__(self, *exc):
        await self.ws.close()

    async def send(self, *cmds: dict) -> None:
        await self.ws.send(json.dumps(list(cmds)))

    async def _expect(self, name: str, timeout: float = 15.0) -> dict:
        async def loop():
            while True:
                for i, cmd in enumerate(self.inbox):
                    if cmd["cmd"] == name:
                        return self.inbox.pop(i)
                for cmd in json.loads(await self.ws.recv()):
                    if cmd["cmd"] == "ConnectionRefused":
                        raise RuntimeError(f"refused: {cmd}")
                    self.inbox.append(cmd)
        return await asyncio.wait_for(loop(), timeout)

    async def get(self, key: str):
        await self.send({"cmd": "Get", "keys": [key]})
        return (await self._expect("Retrieved"))["keys"][key]


async def wait_for(predicate, timeout=20.0, what="condition"):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{what} not met in {timeout}s")


def controller_for(url, data_dir, sim, ledger, catalogue, bus):
    cfg = ClientConfig(server=url, slot_name="Wout", data_dir=data_dir, poll_interval=0.1)
    return SyncController(cfg, sim, ledger, catalogue, bus)


async def test_full_multiworld_session(multiworld, tmp_path, catalogue):
    url = multiworld["url"]
    data_dir = tmp_path / "data"
    sim = SimulatedGameAdapter(tmp_path / "sim.json")
    bus = ev.EventBus()
    log: list[ev.Event] = []
    bus.subscribe(lambda e: e.kind != ev.STATUS and log.append(e))

    async with ScriptedClient(url, "Partner", "ChecksFinder") as partner:
        cd_slot = next(int(k) for k, v in partner.connected["slot_info"].items() if v["game"] == "Crimson Desert")
        team = partner.connected["team"]
        partner_locations = partner.connected["missing_locations"]

        # --- session 1: connect, play every Crimson Desert check, receive items
        ledger = Ledger(data_dir / "ledger.sqlite3")
        c = controller_for(url, data_dir, sim, ledger, catalogue, bus)
        task = asyncio.create_task(c.run())
        try:
            await wait_for(lambda: c.authenticated, what="Crimson Desert client connected")
            assert c.slot == cd_slot and c.session.seed_name

            enabled = sorted(c.enabled_location_ids)
            for loc in catalogue.locations.values():
                if loc.ap_id in enabled:
                    sim.complete_location(loc.name)
            await wait_for(lambda: set(enabled) <= c.server_checked, what="all CD checks confirmed by the server")

            # the partner finishes its world too, sending its share of our items
            await partner.send({"cmd": "LocationChecks", "locations": partner_locations})

            partner_slot = partner.connected["slot"]
            await wait_for(lambda: any(r.sender == partner_slot for r in ledger.items(c.session.id)),
                           what="an item arrived from the ChecksFinder world")

            def all_delivered():
                items = ledger.items(c.session.id)
                return items and all(r.state is DeliveryState.COMPLETE for r in items)
            # both worlds are fully checked, so the item list is final once it stops growing
            previous = -1
            while previous != len(ledger.items(c.session.id)):
                previous = len(ledger.items(c.session.id))
                await wait_for(all_delivered, what="every received item delivered")
                await asyncio.sleep(1.0)
            received = ledger.items(c.session.id)
            received_s1 = len(received)
            inventory_s1 = sim.get_inventory_snapshot()
            assert [r.idx for r in received] == list(range(received_s1)), "ledger indices are contiguous"
            assert sum(inventory_s1.values()) == received_s1, "each received item delivered exactly once"
            assert {r.sender for r in received} >= {cd_slot, partner_slot}
            assert any(e.kind == ev.ITEM_SENT for e in log), "an item went from Crimson Desert to ChecksFinder"
        finally:
            c.stop()
            await asyncio.wait_for(task, 10)
            ledger.close()

        # --- session 2: a fresh client process on the same ledger receives the full list again: no duplicates
        ledger = Ledger(data_dir / "ledger.sqlite3")
        c2 = controller_for(url, data_dir, sim, ledger, catalogue, bus)
        task = asyncio.create_task(c2.run())
        try:
            await wait_for(lambda: c2.authenticated, what="reconnected")
            await asyncio.sleep(1.0)  # let the replayed ReceivedItems(index 0) arrive and be processed
            assert len(ledger.items(c2.session.id)) == received_s1
            assert sim.get_inventory_snapshot() == inventory_s1

            # --- victory
            sim.update(goal_complete=True)
            await wait_for(lambda: ledger.session(c2.session.id).goal_reported, what="goal reported")
            status = None
            for _ in range(50):
                status = await partner.get(f"_read_client_status_{team}_{cd_slot}")
                if status == 30:
                    break
                await asyncio.sleep(0.1)
            assert status == 30, f"server shows client status {status}, expected 30 (goal)"
        finally:
            c2.stop()
            await asyncio.wait_for(task, 10)
            ledger.close()


async def test_wrong_game_slot_is_refused(multiworld, tmp_path, catalogue):
    sim = SimulatedGameAdapter(tmp_path / "sim.json")
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    try:
        cfg = ClientConfig(server=multiworld["url"], slot_name="Partner", data_dir=tmp_path, poll_interval=0.1)
        c = SyncController(cfg, sim, ledger, catalogue)
        await asyncio.wait_for(c.run(), 20)
        assert c.connection.fatal_error
        assert c.session is None and sim.get_inventory_snapshot() == {}
    finally:
        ledger.close()
