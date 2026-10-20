"""A minimal in-process Archipelago server for fast, deterministic client tests."""

from __future__ import annotations

import asyncio
import json

import websockets


class FakeAPServer:
    def __init__(self, *, seed="FAKESEED", slot_name="Wout", game="Crimson Desert", slot_data=None, password=""):
        self.seed, self.slot_name, self.game, self.password = seed, slot_name, game, password
        self.slot_data = slot_data or {}
        self.items: list[dict] = []  # NetworkItems for slot 1
        self.checked: set[int] = set()
        self.status: list[int] = []
        self.received_cmds: list[dict] = []
        self.clients: set = set()
        self.server = None
        self.port = None
        self.connected_event = asyncio.Event()

    async def start(self):
        self.server = await websockets.serve(self._handler, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()

    @property
    def url(self):
        return f"ws://127.0.0.1:{self.port}"

    async def drop_clients(self):
        for ws in list(self.clients):
            await ws.close()

    async def push_item(self, item_id: int, location: int = 1, player: int = 2, flags: int = 1):
        self.items.append({"item": item_id, "location": location, "player": player, "flags": flags,
                           "class": "NetworkItem"})
        await self.broadcast([{"cmd": "ReceivedItems", "index": len(self.items) - 1, "items": [self.items[-1]]}])

    async def broadcast(self, cmds):
        for ws in list(self.clients):
            try:
                await ws.send(json.dumps(cmds))
            except websockets.ConnectionClosed:
                self.clients.discard(ws)

    async def _handler(self, ws, *args):
        self.clients.add(ws)
        try:
            await ws.send(json.dumps([{"cmd": "RoomInfo", "seed_name": self.seed, "games": [self.game],
                                       "datapackage_checksums": {}, "password": bool(self.password),
                                       "version": {"major": 0, "minor": 6, "build": 9, "class": "Version"}}]))
            async for message in ws:
                for cmd in json.loads(message):
                    self.received_cmds.append(cmd)
                    await self._handle(ws, cmd)
        except websockets.ConnectionClosed:
            pass
        finally:
            self.clients.discard(ws)

    async def _handle(self, ws, cmd):
        kind = cmd["cmd"]
        if kind == "Connect":
            errors = []
            if cmd["name"] != self.slot_name:
                errors.append("InvalidSlot")
            if cmd.get("password", "") != self.password:
                errors.append("InvalidPassword")
            if errors:
                await ws.send(json.dumps([{"cmd": "ConnectionRefused", "errors": errors}]))
                return
            await ws.send(json.dumps([
                {"cmd": "Connected", "team": 0, "slot": 1,
                 "players": [{"team": 0, "slot": 1, "alias": "Wout", "name": "Wout"},
                             {"team": 0, "slot": 2, "alias": "Friend", "name": "Friend"}],
                 "missing_locations": [], "checked_locations": sorted(self.checked),
                 "slot_data": self.slot_data,
                 "slot_info": {"1": {"name": "Wout", "game": self.game, "type": 1},
                               "2": {"name": "Friend", "game": "APQuest", "type": 1}},
                 "hint_points": 0},
                {"cmd": "ReceivedItems", "index": 0, "items": self.items},
            ]))
            self.connected_event.set()
        elif kind == "LocationChecks":
            new = set(cmd["locations"]) - self.checked
            self.checked |= set(cmd["locations"])
            if new:
                await self.broadcast([{"cmd": "RoomUpdate", "checked_locations": sorted(new)}])
        elif kind == "StatusUpdate":
            self.status.append(cmd["status"])
        elif kind == "Sync":
            await ws.send(json.dumps([{"cmd": "ReceivedItems", "index": 0, "items": self.items}]))
