"""Archipelago network protocol: packet builders and typed views of server packets.

Reference: docs/network protocol.md in ArchipelagoMW/Archipelago.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass
from typing import Any, Iterable

GAME = "Crimson Desert"
CLIENT_VERSION = (0, 6, 7)

# items_handling: other worlds + own world + starting inventory
ITEMS_HANDLING_ALL = 0b111


class ClientStatus(enum.IntEnum):
    UNKNOWN = 0
    CONNECTED = 5
    READY = 10
    PLAYING = 20
    GOAL = 30


class ItemFlags(enum.IntFlag):
    NONE = 0
    PROGRESSION = 0b001
    USEFUL = 0b010
    TRAP = 0b100


@dataclass(frozen=True)
class NetworkItem:
    item: int
    location: int
    player: int  # sending player
    flags: int = 0

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> NetworkItem:
        return cls(int(data["item"]), int(data["location"]), int(data["player"]), int(data.get("flags", 0)))


def network_version(version: tuple[int, int, int] = CLIENT_VERSION) -> dict[str, Any]:
    major, minor, build = version
    return {"major": major, "minor": minor, "build": build, "class": "Version"}


def encode(commands: Iterable[dict[str, Any]]) -> str:
    return json.dumps(list(commands), separators=(",", ":"))


def decode(message: str | bytes) -> list[dict[str, Any]]:
    data = json.loads(message)
    if not isinstance(data, list):
        raise ValueError("Archipelago packets must be JSON arrays")
    return [cmd for cmd in data if isinstance(cmd, dict) and "cmd" in cmd]


# ------------------------------------------------------------------ client -> server
def connect(name: str, password: str | None, uuid: str, *, tags: list[str] | None = None) -> dict[str, Any]:
    return {
        "cmd": "Connect",
        "game": GAME,
        "name": name,
        "password": password or "",
        "uuid": uuid,
        "version": network_version(),
        "items_handling": ITEMS_HANDLING_ALL,
        "tags": tags or [],
        "slot_data": True,
    }


def location_checks(location_ids: Iterable[int]) -> dict[str, Any]:
    return {"cmd": "LocationChecks", "locations": sorted(set(int(i) for i in location_ids))}


def status_update(status: ClientStatus) -> dict[str, Any]:
    return {"cmd": "StatusUpdate", "status": int(status)}


def sync() -> dict[str, Any]:
    return {"cmd": "Sync"}


def get_data_package(games: Iterable[str]) -> dict[str, Any]:
    return {"cmd": "GetDataPackage", "games": sorted(set(games))}


def say(text: str) -> dict[str, Any]:
    return {"cmd": "Say", "text": text}


# ------------------------------------------------------------------ server -> client
CONNECTION_REFUSED_HELP = {
    "InvalidSlot": "No player with that slot name exists in this room. Check the spelling (it is case-sensitive).",
    "InvalidGame": "That slot is not a Crimson Desert slot.",
    "IncompatibleVersion": "The server needs a newer client. Update Crimsonipelago.",
    "InvalidPassword": "Wrong room password.",
    "InvalidItemsHandling": "The server rejected the item handling flags (this is a client bug; please report it).",
}


def explain_refusal(errors: list[str]) -> str:
    if not errors:
        return "The server refused the connection without giving a reason."
    return " ".join(CONNECTION_REFUSED_HELP.get(e, f"Server error: {e}.") for e in errors)


def render_print_json(parts: list[dict[str, Any]], names: Any) -> str:
    """Flatten a PrintJSON message using a name resolver (see DataPackageCache)."""
    out = []
    for part in parts:
        kind = part.get("type", "text")
        text = part.get("text", "")
        if kind == "player_id":
            out.append(names.player_name(int(text)))
        elif kind == "item_id":
            out.append(names.item_name(int(text), int(part.get("player", 0))))
        elif kind == "location_id":
            out.append(names.location_name(int(text), int(part.get("player", 0))))
        else:
            out.append(str(text))
    return "".join(out)
