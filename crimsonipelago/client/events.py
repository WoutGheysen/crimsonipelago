"""A tiny publish/subscribe bus for the activity feed (UI, console, ledger log)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

log = logging.getLogger("crimsonipelago")


@dataclass(frozen=True)
class Event:
    kind: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    at: datetime = field(default_factory=datetime.now)


# kinds used by the client; the UI maps these to icons/colours
CHECK_SENT = "check_sent"
CHECK_DETECTED = "check_detected"
ITEM_RECEIVED = "item_received"
ITEM_SENT = "item_sent"  # we found an item for someone else
REWARD_DELIVERED = "reward_delivered"
REWARD_PENDING = "reward_pending"
REWARD_UNSUPPORTED = "reward_unsupported"
RECOVERY_REQUIRED = "recovery_required"
CONNECTION = "connection"
SYNC_PAUSED = "sync_paused"
SYNC_RESTORED = "sync_restored"
GOAL = "goal"
CHAT = "chat"
ERROR = "error"
STATUS = "status"  # status snapshot changed; data has the snapshot


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[Callable[[Event], None]] = []

    def subscribe(self, fn: Callable[[Event], None]) -> Callable[[], None]:
        self._subscribers.append(fn)
        return lambda: self._subscribers.remove(fn)

    def emit(self, kind: str, message: str, **data: Any) -> Event:
        event = Event(kind, message, data)
        level = logging.WARNING if kind in (ERROR, SYNC_PAUSED, RECOVERY_REQUIRED) else logging.INFO
        if kind != STATUS:
            log.log(level, "[%s] %s", kind, message)
        for fn in list(self._subscribers):
            try:
                fn(event)
            except Exception:  # a broken subscriber must not break synchronization
                log.exception("event subscriber failed")
        return event
