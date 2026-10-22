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


class DeliveryEngine:
    def __init__(self, ledger: Ledger, adapter: GameAdapter, catalogue: Catalogue, bus: ev.EventBus):
        self.ledger, self.adapter, self.catalogue, self.bus = ledger, adapter, catalogue, bus
        self.last_waiting_reason: str | None = None

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

    # ------------------------------------------------------------------ delivery
    def deliver_pending(self, session_id: int) -> DeliveryReport:
        pending: list[ReceivedItem] = self.ledger.items(session_id, (DeliveryState.PENDING,))
        recovery = len(self.ledger.items(session_id, (DeliveryState.RECOVERY_REQUIRED,)))
        if not pending:
            self.last_waiting_reason = None
            return DeliveryReport(recovery_required=recovery)
        if recovery:
            # never stack new writes on top of a save in an unknown state
            reason = "a previous delivery needs your decision first"
            return self._waiting(len(pending), reason, recovery)

        safe = self.adapter.safe_to_write()
        if not safe.ok:
            return self._waiting(len(pending), safe.message, recovery)

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

        indices = [e.index for e in entries]
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

    def _waiting(self, pending: int, reason: str, recovery: int) -> DeliveryReport:
        if reason != self.last_waiting_reason:
            self.bus.emit(ev.REWARD_PENDING, f"{pending} reward(s) waiting: {reason}")
            self.last_waiting_reason = reason
        return DeliveryReport(pending=pending, waiting_reason=reason, recovery_required=recovery)
