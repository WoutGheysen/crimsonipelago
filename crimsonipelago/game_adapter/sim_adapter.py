"""A simulated Crimson Desert, stored as a small JSON file.

It lets the whole multiworld loop be exercised before the real game adapter
can write saves: complete a "check" with `crimsonipelago sim complete ...`,
watch it reach the Archipelago server, and watch received items land in the
simulated inventory. It implements the same two-phase write protocol as the
real adapter, plus fault-injection hooks used by the test suite.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from crimson_desert.catalogue import Catalogue, ItemDef

from .interface import DeliveryBatch, GameAdapter, GameVersion, PreparedWrite, Result, SaveIdentity

SIM_VERSION = 1


class SimulatedCrash(RuntimeError):
    """Raised by fault injection to emulate the process dying at a precise point."""


def new_state(profile_id: str = "sim/slot1") -> dict[str, Any]:
    return {
        "sim_version": SIM_VERSION,
        "profile_id": profile_id,
        "game_running": False,
        "completed_locations": [],
        "goal_complete": False,
        "inventory": {},
    }


class SimulatedGameAdapter(GameAdapter):
    name = "simulated"

    def __init__(self, path: str | Path, *, create: bool = True, profile_id: str = "sim/slot1"):
        self.path = Path(path)
        if create and not self.path.exists():
            self.write_state(new_state(profile_id))
        # fault injection (tests)
        self.fail_prepare: str | None = None
        self.fail_commit: str | None = None
        self.crash_after_commit = False
        self.crash_before_commit = False
        self.ungrantable: set[str] = set()

    # ------------------------------------------------------------------ state file
    def read_state(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def write_state(self, state: dict[str, Any]) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def update(self, **changes: Any) -> None:
        state = self.read_state()
        state.update(changes)
        self.write_state(state)

    def complete_location(self, name: str) -> None:
        state = self.read_state()
        if name not in state["completed_locations"]:
            state["completed_locations"].append(name)
        self.write_state(state)

    # ------------------------------------------------------------------ reading
    def get_game_version(self) -> GameVersion:
        return GameVersion("simulated", "simulated game", supported=True, writes_allowed=True)

    def get_save_identity(self) -> SaveIdentity | None:
        if not self.path.exists():
            return None
        return SaveIdentity(self.read_state()["profile_id"], str(self.path))

    def get_completed_checks(self, catalogue: Catalogue) -> set[int]:
        if not self.path.exists():
            return set()
        names = self.read_state()["completed_locations"]
        return {catalogue.locations[n].ap_id for n in names if n in catalogue.locations}

    def is_goal_complete(self, catalogue: Catalogue, goal_key: str) -> bool:
        return bool(self.path.exists() and self.read_state().get("goal_complete"))

    def get_inventory_snapshot(self) -> dict[str, int]:
        return dict(self.read_state()["inventory"])

    # ------------------------------------------------------------------ writing
    def missing_unlocks(self, items) -> list[ItemDef]:
        inventory = self.get_inventory_snapshot()
        return [i for i in items if (i.game_ref or {}).get("kind") == "knowledge" and not inventory.get(i.name)]

    def can_grant_item(self, item: ItemDef) -> Result:
        if item.name in self.ungrantable:
            return Result.unsupported(f"{item.name!r} cannot be delivered (simulated restriction)")
        return Result.success()

    def safe_to_write(self) -> Result:
        if not self.path.exists():
            return Result.failure("simulated save missing")
        if self.read_state().get("game_running"):
            return Result.failure("the simulated game is running; rewards are applied when it closes")
        return Result.success()

    def fingerprint(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest() if self.path.exists() else "missing"

    def prepare(self, batch: DeliveryBatch) -> PreparedWrite | Result:
        if self.fail_prepare:
            return Result.failure(self.fail_prepare)
        for entry in batch.entries:
            verdict = self.can_grant_item(entry.item)
            if not verdict.ok:
                return verdict
        safe = self.safe_to_write()
        if not safe.ok:
            return safe
        original = self.path.read_bytes()
        state = json.loads(original)
        for entry in batch.entries:
            inv = state["inventory"]
            inv[entry.item.name] = inv.get(entry.item.name, 0) + entry.item.quantity
        encoded = json.dumps(state, indent=2, sort_keys=True).encode("utf-8")
        temp = self.path.with_name(self.path.name + f".pending-{batch.batch_id[:8]}")
        with open(temp, "wb") as fh:
            fh.write(encoded)
            fh.flush()
            os.fsync(fh.fileno())
        return PreparedWrite(
            batch_id=batch.batch_id,
            pre_fingerprint=hashlib.sha256(original).hexdigest(),
            post_fingerprint=hashlib.sha256(encoded).hexdigest(),
            temp_path=str(temp),
        )

    def commit(self, prepared: PreparedWrite) -> Result:
        if self.crash_before_commit:
            raise SimulatedCrash("crash before commit")
        if self.fail_commit:
            return Result.failure(self.fail_commit)
        if self.fingerprint() != prepared.pre_fingerprint:
            return Result.failure("the simulated save changed after the delivery was prepared")
        if self.read_state().get("game_running"):
            return Result.failure("the simulated game started before the write")
        os.replace(prepared.temp_path, self.path)
        if self.crash_after_commit:
            raise SimulatedCrash("crash after commit, before the client recorded it")
        return self.verify_reward(prepared)

    def discard(self, prepared: PreparedWrite) -> None:
        if prepared.temp_path:
            try:
                os.remove(prepared.temp_path)
            except FileNotFoundError:
                pass
