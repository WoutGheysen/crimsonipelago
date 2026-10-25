"""Exactly-once effective reward delivery (scope doc 5.3).

    RECEIVED -> VALIDATED -> PENDING -> APPLYING -> VERIFIED -> COMPLETE
                                         APPLYING -> RECOVERY_REQUIRED
    RECEIVED -> UNSUPPORTED   (parked; re-validated when the catalogue/adapter changes)

Crash safety comes from the two-phase write in the adapter interface: the
ledger records the save's fingerprint before and after the write *before*
the save is swapped. On restart, comparing the current fingerprint with
those two values says unambiguously whether the write happened.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from crimson_desert.catalogue import Catalogue

from ..game_adapter.interface import DeliveryBatch, DeliveryEntry, GameAdapter, PreparedWrite, Result, Status
from . import events as ev
from .database import BatchState, DeliveryState, Ledger, ReceivedItem

MAX_BATCH = 50


@dataclass(frozen=True)
class DeliveryReport:
    delivered: int = 0
    pending: int = 0
    waiting_reason: str | None = None
    recovery_required: int = 0


class LiveDelivery(Protocol):
    """Giving items in the running game (game_adapter/runtime/live.py)."""

    learns_knowledge: bool          # knowledge (abilities) can go in live too, for this game build

    def ready(self) -> bool: ...
    def poll(self) -> None: ...
    def give(self, item_key: int, count: int): ...   # -> LiveResult
    def give_knowledge(self, key: int): ...          # -> LiveResult
    # Optional, for noticing a save being loaded (LiveChannel has them):
    #   loads: int                   saves loaded while connected
    #   load_at: float               wall-clock time the latest load was noticed
    #   last_seen_unix: int | None   the game's time when it was last seen running


LIVE_KNOWLEDGE_TRIES = 3     # knowledge refused live this often waits for the save instead
LIVE_COMBINE_MAX = 9999      # the most of one item given live in one go (copies waiting together are combined)


def _given_at(rec: ReceivedItem) -> float:
    """When an IN_GAME item was given (its last state change)."""
    try:
        return datetime.fromisoformat(rec.updated_at).timestamp()
    except ValueError:
        return 0.0


class DeliveryEngine:
    def __init__(self, ledger: Ledger, adapter: GameAdapter, catalogue: Catalogue, bus: ev.EventBus,
                 live: LiveDelivery | None = None):
        self.ledger, self.adapter, self.catalogue, self.bus = ledger, adapter, catalogue, bus
        self.live = live
        self.last_waiting_reason: str | None = None
        self._loads_seen: int | None = None

    def _name(self, item_id: int) -> str:
        item = self.catalogue.item_by_id(item_id)
        return item.name if item else f"unknown item {item_id}"

    # ------------------------------------------------------------------ validation
    def validate(self, session_id: int) -> None:
        for rec in self.ledger.items(session_id, (DeliveryState.RECEIVED,)):
            item = self.catalogue.item_by_id(rec.item_id)
            if item is None:
                self.ledger.set_item_state(session_id, [rec.idx], DeliveryState.UNSUPPORTED,
                                           error=f"item id {rec.item_id} is not in this client's catalogue")
                self.bus.emit(ev.REWARD_UNSUPPORTED, f"Received an item this client does not know (id {rec.item_id}). "
                                                     "Update Crimsonipelago; it will be delivered then.")
                continue
            verdict = self.adapter.can_grant_item(item)
            if verdict.status is Status.OK:
                self.ledger.set_item_state(session_id, [rec.idx], DeliveryState.VALIDATED)
                self.ledger.set_item_state(session_id, [rec.idx], DeliveryState.PENDING)
            elif verdict.status is Status.UNSUPPORTED:
                self.ledger.set_item_state(session_id, [rec.idx], DeliveryState.UNSUPPORTED, error=verdict.message)
                self.bus.emit(ev.REWARD_UNSUPPORTED, f"{item.name}: kept for later, cannot be delivered yet "
                                                     f"({verdict.message})", index=rec.idx)
            else:
                self.ledger.set_item_state(session_id, [rec.idx], DeliveryState.RECEIVED, error=verdict.message)

    def revalidate_unsupported(self, session_id: int) -> None:
        parked = [r.idx for r in self.ledger.items(session_id, (DeliveryState.UNSUPPORTED,))]
        self.ledger.set_item_state(session_id, parked, DeliveryState.RECEIVED)

    # ------------------------------------------------------------------ recovery
    def _prepared(self, batch) -> PreparedWrite:
        return PreparedWrite(batch.batch_id, batch.pre_fingerprint, batch.post_fingerprint, batch.temp_path)

    def recover(self, session_id: int) -> None:
        """Resolve writes that were in flight when the client last stopped."""
        for batch in self.ledger.batches(session_id, (BatchState.PREPARED,)):
            prepared = self._prepared(batch)
            outcome = self.adapter.recover_pending_delivery(prepared)
            if outcome == "applied":
                self._complete(session_id, batch.batch_id, "recovered after restart")
            elif outcome == "not_applied":
                self.adapter.discard(prepared)
                self.ledger.finish_batch(batch.batch_id, BatchState.ABANDONED, DeliveryState.PENDING,
                                         error="interrupted before the save was written; will retry")
            else:
                self._needs_recovery(batch.batch_id, "the save changed since the interrupted delivery started")

    def resolve_recovery(self, session_id: int, batch_id: str, decision: str) -> None:
        """User decision for a RECOVERY batch: 'delivered' or 'retry'."""
        batches = {b.batch_id: b for b in self.ledger.batches(session_id, (BatchState.RECOVERY,))}
        if batch_id not in batches:
            raise ValueError(f"no delivery {batch_id} needs recovery")
        indices = [r.idx for r in self.ledger.batch_items(batch_id)]
        if decision == "delivered":
            self.ledger.set_item_state(session_id, indices, DeliveryState.COMPLETE,
                                       expect=(DeliveryState.RECOVERY_REQUIRED,))
            self.ledger.finish_batch(batch_id, BatchState.COMMITTED, DeliveryState.COMPLETE)
            self.bus.emit(ev.REWARD_DELIVERED, f"Marked {len(indices)} reward(s) as delivered by user decision")
        elif decision == "retry":
            self.adapter.discard(self._prepared(batches[batch_id]))
            self.ledger.set_item_state(session_id, indices, DeliveryState.PENDING,
                                       expect=(DeliveryState.RECOVERY_REQUIRED,))
            self.ledger.finish_batch(batch_id, BatchState.ABANDONED, DeliveryState.PENDING)
            self.bus.emit(ev.REWARD_PENDING, f"{len(indices)} reward(s) will be delivered again")
        else:
            raise ValueError("decision must be 'delivered' or 'retry'")

    def _needs_recovery(self, batch_id: str, why: str) -> None:
        self.ledger.finish_batch(batch_id, BatchState.RECOVERY, DeliveryState.RECOVERY_REQUIRED, error=why)
        self.bus.emit(ev.RECOVERY_REQUIRED,
                      f"A reward delivery was interrupted and the save changed afterwards ({why}). "
                      f"Check your inventory, then choose 'delivered' or 'retry' for delivery {batch_id[:8]}.",
                      batch_id=batch_id)

    def _complete(self, session_id: int, batch_id: str, how: str) -> None:
        self.ledger.finish_batch(batch_id, BatchState.COMMITTED, DeliveryState.VERIFIED)
        items = self.ledger.batch_items(batch_id)
        self.ledger.set_item_state(session_id, [r.idx for r in items], DeliveryState.COMPLETE,
                                   expect=(DeliveryState.VERIFIED,))
        names = ", ".join(self._name(r.item_id) for r in items)
        self.bus.emit(ev.REWARD_DELIVERED, f"Delivered: {names}" + (f" ({how})" if how else ""),
                      indices=[r.idx for r in items])

    def reconcile(self, session_id: int) -> int:
        """Re-queue delivered unlocks the loaded save no longer has (e.g. the player loaded an older save).

        Only the adapter's idempotent kinds qualify, so delivering one again can never duplicate it."""
        done = self.ledger.items(session_id, (DeliveryState.COMPLETE,))
        items = {r.item_id: self.catalogue.item_by_id(r.item_id) for r in done}
        missing = {i.ap_id for i in self.adapter.missing_unlocks([i for i in items.values() if i is not None])}
        again = [r.idx for r in done if r.item_id in missing]
        if again:
            self.ledger.set_item_state(session_id, again, DeliveryState.PENDING, expect=(DeliveryState.COMPLETE,),
                                       error="not in the loaded save (an older save was loaded?); delivering again")
            names = ", ".join(sorted({items[r.item_id].name for r in done if r.idx in again}))
            self.bus.emit(ev.REWARD_PENDING, f"The loaded save is missing {names}; it will be given again")
        return len(again)

    # ------------------------------------------------------------------ live delivery
    def _live_kind(self, rec: ReceivedItem) -> bool:
        item = self.catalogue.item_by_id(rec.item_id)
        kind = (item.game_ref or {}).get("kind") if item is not None else None
        return kind == "inventory_item" or (kind == "knowledge" and bool(getattr(self.live, "learns_knowledge", False)))

    def _given_up_live(self, rec: ReceivedItem) -> bool:
        """Knowledge the game refused live a few times (each on a later tick) is left for the save."""
        item = self.catalogue.item_by_id(rec.item_id)
        kind = (item.game_ref or {}).get("kind") if item is not None else None
        return kind == "knowledge" and rec.attempts >= LIVE_KNOWLEDGE_TRIES

    def _live_units(self, candidates: list[ReceivedItem]) -> list[tuple[list[ReceivedItem], dict, int]]:
        """What to give, in order: copies of the same item waiting together go in as one delivery (the game takes
        8 deliveries between two of its own transactions, however big each is: live log of 25 Oct), up to a stack."""
        units: list[tuple[list[ReceivedItem], dict, int]] = []
        open_units: dict[int, int] = {}                     # item key -> index of its unit still taking more
        for rec in candidates:
            ref = self.catalogue.item_by_id(rec.item_id).game_ref
            if ref["kind"] != "inventory_item":
                units.append(([rec], ref, 1))
                continue
            key, count = int(ref["item_key"]), int(ref.get("count", 1))
            limit = min(int(ref.get("max_stack") or LIVE_COMBINE_MAX), LIVE_COMBINE_MAX)
            at = open_units.get(key)
            if at is not None and units[at][2] + count <= limit:
                recs, first, total = units[at]
                units[at] = (recs + [rec], first, total + count)
                continue
            open_units[key] = len(units)
            units.append(([rec], ref, count))
        return units

    def _deliver_live(self, session_id: int, pending: list[ReceivedItem]) -> tuple[list[ReceivedItem], str | None]:
        """Give what can be given in the running game. Returns what's left, and why live delivery is waiting."""
        candidates = [r for r in pending if self._live_kind(r) and not self._given_up_live(r)]
        if self.live is None or not candidates:
            return pending, None
        if not self.live.ready():
            return pending, (getattr(self.live, "unsupported", None) or "the game plugin isn't connected")
        left = [r for r in pending if r not in candidates]
        given: list[ReceivedItem] = []
        waiting = None
        units = self._live_units(candidates)
        for n, (recs, ref, total) in enumerate(units):
            if ref["kind"] == "knowledge":
                result = self.live.give_knowledge(int(ref["knowledge_key"]))
            else:
                result = self.live.give(int(ref["item_key"]), total)
            indices = [r.idx for r in recs]
            if result.given:
                self.ledger.set_item_state(session_id, indices, DeliveryState.IN_GAME, expect=(DeliveryState.PENDING,))
                given.extend(recs)
            elif result.state in ("refused", "unknown item"):
                again = ref["kind"] == "knowledge" and result.state == "refused" and recs[0].attempts + 1 < LIVE_KNOWLEDGE_TRIES
                self.bus.emit(ev.REWARD_PENDING, f"{self._name(recs[0].item_id)}: {result.message}; " +
                              ("trying again in a moment" if again else "it goes into the save once you quit to desktop"),
                              index=recs[0].idx)
                self.ledger.set_item_state(session_id, indices, DeliveryState.PENDING, bump_attempts=True,
                                           error=f"not given in the game: {result.message}; " +
                                                 ("trying again in a moment" if again else
                                                  "it will be added to the save once you quit to desktop"))
                left.extend(recs)
            else:
                waiting = result.message
                left.extend(r for unit in units[n:] for r in unit[0])
                break
        if given:
            names = ", ".join(self._name(r.item_id) for r in given)
            self.bus.emit(ev.REWARD_DELIVERED, f"Delivered in the game: {names}", indices=[r.idx for r in given],
                          live=True)
        return sorted(left, key=lambda r: r.idx), waiting

    def notice_loads(self, session_id: int) -> None:
        """A save loaded while items given live weren't saved yet: they're gone from the game, give them again.
        Whatever was loaded predates them, since no save after them has been seen with the game still going."""
        loads = getattr(self.live, "loads", None)
        if loads is None:
            return
        if self._loads_seen is not None and loads > self._loads_seen:
            noticed = float(getattr(self.live, "load_at", 0.0) or 0.0) or float("inf")
            lost = [r for r in self.ledger.items(session_id, (DeliveryState.IN_GAME,))
                    if _given_at(r) < noticed]       # given after it noticed the load: given into the loaded game
            if lost:
                self.ledger.set_item_state(session_id, [r.idx for r in lost], DeliveryState.PENDING,
                                           expect=(DeliveryState.IN_GAME,),
                                           error="a save was loaded before the game saved them; giving them again")
                names = ", ".join(sorted({self._name(r.item_id) for r in lost}))
                self.bus.emit(ev.REWARD_PENDING, f"A save was loaded before the game saved {names}; "
                                                 "they will be given again")
        self._loads_seen = loads

    def settle_in_game(self, session_id: int) -> None:
        """Items given live are complete once the game saves after them. If the game closes without saving,
        they were never saved: give them again.

        While the game runs, a save after them isn't enough on its own: a save loaded just before it would have
        taken them away (the autosave then saves the loaded state). So they also wait until the game has been seen
        after that save, by which time notice_loads has seen any load in between."""
        live = self.ledger.items(session_id, (DeliveryState.IN_GAME,))
        if not live:
            return

        given_at = _given_at
        running = self.adapter.is_game_running()
        watches_loads = self.live is not None and hasattr(self.live, "loads")
        seen = getattr(self.live, "last_seen_unix", None)
        seen_since_save = seen is not None and not self.adapter.save_written_after(float(seen))
        saved = [r for r in live if self.adapter.save_written_after(given_at(r))
                 and (running is False or not watches_loads or seen_since_save)]
        self.ledger.set_item_state(session_id, [r.idx for r in saved], DeliveryState.COMPLETE,
                                   expect=(DeliveryState.IN_GAME,))
        unsaved = [r for r in live if r not in saved and not self.adapter.save_written_after(given_at(r))]
        if unsaved and running is False:
            self.ledger.set_item_state(session_id, [r.idx for r in unsaved], DeliveryState.PENDING,
                                       expect=(DeliveryState.IN_GAME,),
                                       error="the game closed before saving them; giving them again")
            names = ", ".join(sorted({self._name(r.item_id) for r in unsaved}))
            self.bus.emit(ev.REWARD_PENDING, f"The game closed without saving after {names} arrived; "
                                             "they will be given again")

    # ------------------------------------------------------------------ delivery
    def deliver_pending(self, session_id: int) -> DeliveryReport:
        if self.live is not None:
            self.live.poll()
            self.notice_loads(session_id)
        self.settle_in_game(session_id)
        pending: list[ReceivedItem] = self.ledger.items(session_id, (DeliveryState.PENDING,))
        recovery = len(self.ledger.items(session_id, (DeliveryState.RECOVERY_REQUIRED,)))
        if not pending:
            self.last_waiting_reason = None
            return DeliveryReport(recovery_required=recovery)
        # items with nothing to write complete right away, game running or not
        no_write = [r for r in pending if (item := self.catalogue.item_by_id(r.item_id)) is not None
                    and not self.adapter.needs_write(item)]
        if no_write:
            self.ledger.set_item_state(session_id, [r.idx for r in no_write], DeliveryState.COMPLETE,
                                       expect=(DeliveryState.PENDING,))
            names = ", ".join(sorted({self._name(r.item_id) for r in no_write}))
            self.bus.emit(ev.REWARD_DELIVERED, f"Received: {names} (nothing to add to the game)",
                          indices=[r.idx for r in no_write])
            pending = [r for r in pending if r not in no_write]
            if not pending:
                self.last_waiting_reason = None
                return DeliveryReport(delivered=len(no_write), recovery_required=recovery)
        before = len(pending)
        pending, live_waiting = self._deliver_live(session_id, pending)
        delivered_live = before - len(pending)
        if not pending:
            self.last_waiting_reason = None
            return DeliveryReport(delivered=delivered_live, recovery_required=recovery)
        if recovery:
            # never stack new writes on top of a save in an unknown state
            reason = "a previous delivery needs your decision first"
            return self._waiting(len(pending), reason, recovery)

        safe = self.adapter.safe_to_write()
        if not safe.ok:
            if live_waiting and all(self._live_kind(r) for r in pending):
                return self._waiting(len(pending), live_waiting, recovery, delivered_live)
            in_game = sum(1 for r in pending if self._live_kind(r) and not self._given_up_live(r))
            if live_waiting and in_game:
                # say both: why the save can't be written, and why the ones that go in live haven't
                reason = f"{safe.message} ({in_game} of them go in while you play, but: {live_waiting})"
                return self._waiting(len(pending), reason, recovery, delivered_live)
            return self._waiting(len(pending), safe.message, recovery, delivered_live)

        chunk = pending[:MAX_BATCH]
        entries = []
        for rec in chunk:
            item = self.catalogue.item_by_id(rec.item_id)
            if item is not None:
                entries.append(DeliveryEntry(rec.idx, item))
        batch = DeliveryBatch(uuid.uuid4().hex, entries)
        prepared = self.adapter.prepare(batch)

        if isinstance(prepared, Result):
            if prepared.status is Status.UNSUPPORTED:
                # find which items are the problem and park only those
                for entry in entries:
                    verdict = self.adapter.can_grant_item(entry.item)
                    if verdict.status is Status.UNSUPPORTED:
                        self.ledger.set_item_state(session_id, [entry.index], DeliveryState.UNSUPPORTED,
                                                   error=verdict.message)
                return self._waiting(len(pending), prepared.message, recovery)
            self.ledger.set_item_state(session_id, [e.index for e in entries], DeliveryState.PENDING,
                                       error=prepared.message, bump_attempts=True)
            return self._waiting(len(pending), prepared.message, recovery)

        skipped = {int(k): v for k, v in (prepared.plan or {}).get("skipped", {}).items()}
        if skipped:
            self.ledger.set_item_state(session_id, list(skipped), DeliveryState.PENDING,
                                       error="; ".join(skipped.values()), bump_attempts=True)
            self._waiting(len(skipped), "; ".join(dict.fromkeys(skipped.values())), recovery)
        indices = [e.index for e in entries if e.index not in skipped]
        try:
            self.ledger.begin_batch(session_id, batch.batch_id, indices, prepared.pre_fingerprint,
                                    prepared.post_fingerprint, prepared.temp_path)
        except Exception:
            self.adapter.discard(prepared)
            raise

        result = self.adapter.commit(prepared)  # a crash here is resolved by recover() on the next start
        if result.ok:
            self._complete(session_id, batch.batch_id, "")
            remaining = len(pending) - len(indices)
            if not skipped:
                self.last_waiting_reason = None
            return DeliveryReport(delivered=len(indices), pending=remaining, recovery_required=recovery)

        outcome = self.adapter.recover_pending_delivery(prepared)
        if outcome == "applied":
            self._complete(session_id, batch.batch_id, "verified after a commit warning")
            return DeliveryReport(delivered=len(indices), pending=len(pending) - len(indices))
        if outcome == "not_applied":
            self.adapter.discard(prepared)
            self.ledger.finish_batch(batch.batch_id, BatchState.ABANDONED, DeliveryState.PENDING, error=result.message)
            return self._waiting(len(pending), result.message, recovery)
        self._needs_recovery(batch.batch_id, result.message)
        return DeliveryReport(pending=len(pending) - len(indices), recovery_required=recovery + len(indices))

    def _waiting(self, pending: int, reason: str, recovery: int, delivered: int = 0) -> DeliveryReport:
        if reason != self.last_waiting_reason:
            self.bus.emit(ev.REWARD_PENDING, f"{pending} reward(s) waiting: {reason}")
            self.last_waiting_reason = reason
        return DeliveryReport(delivered=delivered, pending=pending, waiting_reason=reason, recovery_required=recovery)
