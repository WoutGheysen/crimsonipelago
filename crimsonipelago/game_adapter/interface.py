"""The contract between the Crimsonipelago client and a game adapter (scope doc 6.1/6.2).

Writes follow a two-phase protocol so a crash can never leave the client
unsure whether a reward was applied:

    prepare(batch)  -> PreparedWrite   builds the new save in a temp file and
                                       reports the save fingerprint before and after
    (client durably records both fingerprints)
    commit(prepared)                   atomically swaps the temp file in
    fingerprint()                      on recovery, tells the client which side
                                       of the swap the save is on

If the current fingerprint equals `pre`, the write never happened and is safe to
retry; if it equals `post`, it happened; anything else means the game or the
user changed the save since, and the client stops and asks.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from crimson_desert.catalogue import Catalogue, ItemDef


class Status(enum.Enum):
    OK = "ok"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class Result:
    status: Status
    message: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status is Status.OK

    @classmethod
    def success(cls, message: str = "", **detail: Any) -> Result:
        return cls(Status.OK, message, detail)

    @classmethod
    def failure(cls, message: str, **detail: Any) -> Result:
        return cls(Status.FAILED, message, detail)

    @classmethod
    def unsupported(cls, message: str, **detail: Any) -> Result:
        return cls(Status.UNSUPPORTED, message, detail)


@dataclass(frozen=True)
class GameVersion:
    build: str | None  # Steam build id, or None when unknown
    label: str  # human readable
    supported: bool  # listed in compatibility.json
    writes_allowed: bool  # listed with writes enabled
    note: str = ""


@dataclass(frozen=True)
class SaveIdentity:
    profile_id: str  # stable per save profile; used to bind a seed to one campaign
    path: str | None = None


@dataclass(frozen=True)
class DeliveryEntry:
    index: int  # ReceivedItems index
    item: ItemDef


@dataclass(frozen=True)
class DeliveryBatch:
    batch_id: str
    entries: Sequence[DeliveryEntry]


@dataclass(frozen=True)
class PreparedWrite:
    batch_id: str
    pre_fingerprint: str
    post_fingerprint: str
    temp_path: str | None
    plan: Mapping[str, Any] = field(default_factory=dict)


class GameAdapter(ABC):
    """Read and (where proven safe) modify Crimson Desert state."""

    name: str = "abstract"

    # ------------------------------------------------------------------ reading
    @abstractmethod
    def get_game_version(self) -> GameVersion: ...

    @abstractmethod
    def get_save_identity(self) -> SaveIdentity | None: ...

    @abstractmethod
    def get_completed_checks(self, catalogue: Catalogue) -> set[int]:
        """AP location ids whose completion is visible in the current save."""

    @abstractmethod
    def is_goal_complete(self, catalogue: Catalogue, goal_key: str) -> bool: ...

    def get_inventory_snapshot(self) -> dict[str, int]:
        return {}

    def get_unlocked_abilities(self) -> set[str]:
        return set()

    def get_progression_state(self) -> dict[str, Any]:
        return {}

    def get_current_profile(self) -> str | None:
        identity = self.get_save_identity()
        return identity.profile_id if identity else None

    # ------------------------------------------------------------------ writing
    @abstractmethod
    def can_grant_item(self, item: ItemDef) -> Result:
        """OK if this adapter has a verified way to deliver the item."""

    @abstractmethod
    def safe_to_write(self) -> Result:
        """OK when a write may happen right now (game closed, supported version, save readable)."""

    @abstractmethod
    def fingerprint(self) -> str:
        """Content hash of the save as it is right now."""

    @abstractmethod
    def prepare(self, batch: DeliveryBatch) -> PreparedWrite | Result:
        """Build the modified save without touching the real one."""

    @abstractmethod
    def commit(self, prepared: PreparedWrite) -> Result:
        """Atomically replace the save with the prepared one."""

    def discard(self, prepared: PreparedWrite) -> None:
        """Remove a prepared-but-uncommitted temp file, if any."""

    def save_written_after(self, timestamp: float) -> bool:
        """Whether the current save was written after this Unix time. A new seed only binds to a playthrough
        the player saved after connecting the seed, never to an old one that happens to be the newest save."""
        return True

    def needs_write(self, item: ItemDef) -> bool:
        """False for items with no effect in the game (filler): they complete without touching the save."""
        return True

    def missing_unlocks(self, items: Sequence[ItemDef]) -> list[ItemDef]:
        """Of these delivered items, the unlocks the current save does not have (an older save was loaded).

        Only for items whose delivery is idempotent (learning an ability twice is a no-op), so they can
        safely be delivered again. Consumables are never reported here."""
        return []

    def verify_reward(self, prepared: PreparedWrite) -> Result:
        current = self.fingerprint()
        if current == prepared.post_fingerprint:
            return Result.success("save matches the post-delivery fingerprint")
        return Result.failure("save does not match the post-delivery fingerprint", current=current)

    # convenience wrappers matching the scope doc's method names
    def grant_item(self, batch: DeliveryBatch) -> Result:
        prepared = self.prepare(batch)
        if isinstance(prepared, Result):
            return prepared
        return self.commit(prepared)

    grant_ability = grant_item
    grant_progression_unlock = grant_item

    def recover_pending_delivery(self, prepared: PreparedWrite) -> str:
        """'applied', 'not_applied' or 'unknown' for a write that may have been interrupted."""
        current = self.fingerprint()
        if current == prepared.post_fingerprint:
            return "applied"
        if current == prepared.pre_fingerprint:
            return "not_applied"
        return "unknown"
