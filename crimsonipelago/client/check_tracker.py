"""Turn adapter observations into durable location checks (scope doc 5.4).

A check is written to the ledger the moment it is seen, whether or not the
server is reachable, so it can never be lost to a disconnect.
"""

from __future__ import annotations

from crimson_desert.catalogue import Catalogue

from ..game_adapter.interface import GameAdapter
from . import events as ev
from .database import Ledger


class CheckTracker:
    def __init__(self, ledger: Ledger, adapter: GameAdapter, catalogue: Catalogue, bus: ev.EventBus):
        self.ledger, self.adapter, self.catalogue, self.bus = ledger, adapter, catalogue, bus
        self._known_ids = {loc.ap_id for loc in catalogue.locations.values()}

    def poll(self, session_id: int, enabled_ids: set[int] | None = None) -> set[int]:
        """Record newly completed checks; returns the new location ids."""
        seen = self.adapter.get_completed_checks(self.catalogue) & self._known_ids
        if enabled_ids is not None:
            seen &= enabled_ids
        new = self.ledger.record_checks(session_id, seen)
        for loc_id in sorted(new):
            loc = self.catalogue.location_by_id(loc_id)
            self.bus.emit(ev.CHECK_DETECTED, f"Completed check: {loc.name if loc else loc_id}", location_id=loc_id)
        return new

    def poll_goal(self, session_id: int, goal_key: str) -> bool:
        if self.adapter.is_goal_complete(self.catalogue, goal_key):
            session = self.ledger.session(session_id)
            if not session.goal_detected:
                self.ledger.set_goal(session_id, detected=True)
                self.bus.emit(ev.GOAL, "Goal complete!")
            return True
        return False
