"""Archipelago DataPackage cache and name resolution for the activity feed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class DataPackageCache:
    def __init__(self, cache_dir: str | Path | None):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.games: dict[str, dict[str, Any]] = {}
        self.players: dict[int, str] = {}  # slot -> alias
        self.slot_games: dict[int, str] = {}
        self._items: dict[str, dict[int, str]] = {}
        self._locations: dict[str, dict[int, str]] = {}

    # ------------------------------------------------------------------ cache
    def _path(self, game: str, checksum: str) -> Path | None:
        if not self.cache_dir:
            return None
        safe = "".join(ch if ch.isalnum() else "_" for ch in game)
        return self.cache_dir / f"{safe}-{checksum}.json"

    def missing_games(self, checksums: dict[str, str]) -> list[str]:
        missing = []
        for game, checksum in checksums.items():
            path = self._path(game, checksum)
            if path and path.is_file():
                try:
                    self._add(game, json.loads(path.read_text(encoding="utf-8")))
                    continue
                except (OSError, ValueError):
                    pass
            missing.append(game)
        return missing

    def store(self, games: dict[str, dict[str, Any]]) -> None:
        for game, data in games.items():
            self._add(game, data)
            path = self._path(game, data.get("checksum", "nochecksum"))
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data), encoding="utf-8")

    def _add(self, game: str, data: dict[str, Any]) -> None:
        self.games[game] = data
        self._items[game] = {int(v): k for k, v in data.get("item_name_to_id", {}).items()}
        self._locations[game] = {int(v): k for k, v in data.get("location_name_to_id", {}).items()}

    # ------------------------------------------------------------------ names
    def set_players(self, players: list[dict[str, Any]], slot_info: dict[str, Any], team: int) -> None:
        self.players = {int(p["slot"]): p.get("alias") or p.get("name") for p in players if int(p["team"]) == team}
        self.slot_games = {int(slot): info.get("game", "") for slot, info in slot_info.items()}

    def player_name(self, slot: int) -> str:
        return self.players.get(slot, "Archipelago" if slot == 0 else f"player {slot}")

    def item_name(self, item_id: int, owner_slot: int) -> str:
        game = self.slot_games.get(owner_slot, "")
        return self._items.get(game, {}).get(item_id, f"item {item_id}")

    def location_name(self, location_id: int, owner_slot: int) -> str:
        game = self.slot_games.get(owner_slot, "")
        return self._locations.get(game, {}).get(location_id, f"location {location_id}")
