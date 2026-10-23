"""Persistent synchronization ledger (scope doc 5.2), backed by SQLite.

Every fact the client must not forget lives here before it acts on it:
received items by server index, delivery state per item, the fingerprints of
an in-flight save write, and every check detected in the game (sent or not).
"""

from __future__ import annotations

import enum
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 1


class DeliveryState(str, enum.Enum):
    RECEIVED = "RECEIVED"
    VALIDATED = "VALIDATED"
    PENDING = "PENDING"
    APPLYING = "APPLYING"
    VERIFIED = "VERIFIED"
    COMPLETE = "COMPLETE"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    UNSUPPORTED = "UNSUPPORTED"  # parked: no verified way to deliver this item yet


OPEN_STATES = (DeliveryState.RECEIVED, DeliveryState.VALIDATED, DeliveryState.PENDING)


class BatchState(str, enum.Enum):
    PREPARED = "PREPARED"  # temp file written, real save untouched (as far as we know)
    COMMITTED = "COMMITTED"  # save swapped and verified
    ABANDONED = "ABANDONED"  # prepared but never committed; safe to forget
    RECOVERY = "RECOVERY"  # save is in an unknown state; user must decide


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seed_name TEXT NOT NULL,
    team INTEGER NOT NULL,
    slot INTEGER NOT NULL,
    slot_name TEXT NOT NULL,
    server TEXT,
    profile_id TEXT,
    catalogue_fingerprint TEXT,
    world_version TEXT,
    goal_detected INTEGER NOT NULL DEFAULT 0,
    goal_reported INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_connected_at TEXT,
    UNIQUE (seed_name, team, slot)
);
CREATE TABLE IF NOT EXISTS received_items (
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    idx INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    location_id INTEGER NOT NULL,
    sender INTEGER NOT NULL,
    flags INTEGER NOT NULL,
    state TEXT NOT NULL,
    batch_id TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    received_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (session_id, idx)
);
CREATE TABLE IF NOT EXISTS delivery_batches (
    batch_id TEXT PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    state TEXT NOT NULL,
    pre_fingerprint TEXT NOT NULL,
    post_fingerprint TEXT NOT NULL,
    temp_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checks (
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    location_id INTEGER NOT NULL,
    detected_at TEXT,
    sent_at TEXT,
    confirmed_at TEXT,
    PRIMARY KEY (session_id, location_id)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS received_state ON received_items (session_id, state);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class LedgerError(RuntimeError):
    pass


@dataclass(frozen=True)
class Session:
    id: int
    seed_name: str
    team: int
    slot: int
    slot_name: str
    server: str | None
    profile_id: str | None
    catalogue_fingerprint: str | None
    world_version: str | None
    goal_detected: bool
    goal_reported: bool
    created_at: str = ""          # ISO time (UTC) the client first saw this seed


@dataclass(frozen=True)
class ReceivedItem:
    session_id: int
    idx: int
    item_id: int
    location_id: int
    sender: int
    flags: int
    state: DeliveryState
    batch_id: str | None
    attempts: int
    last_error: str | None


@dataclass(frozen=True)
class Batch:
    batch_id: str
    session_id: int
    state: BatchState
    pre_fingerprint: str
    post_fingerprint: str
    temp_path: str | None


class Ledger:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    def _migrate(self) -> None:
        with self._lock:
            self.conn.executescript(_SCHEMA)
            row = self.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None:
                self.conn.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
            elif int(row["value"]) > SCHEMA_VERSION:
                raise LedgerError(
                    f"ledger {self.path} was written by a newer Crimsonipelago (schema {row['value']}); update the client"
                )

    def _one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        """Locked read: the connection is shared with worker threads, so reads take the same lock as writes."""
        with self._lock:
            return self.conn.execute(sql, params).fetchone()

    def _all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    @property
    def schema_version(self) -> int:
        return int(self._one("SELECT value FROM meta WHERE key='schema_version'")["value"])

    # ------------------------------------------------------------------ sessions
    def get_or_create_session(self, seed_name: str, team: int, slot: int, slot_name: str, server: str | None,
                              catalogue_fingerprint: str | None, world_version: str | None) -> Session:
        with self.tx() as c:
            row = c.execute("SELECT * FROM sessions WHERE seed_name=? AND team=? AND slot=?",
                            (seed_name, team, slot)).fetchone()
            if row is None:
                c.execute(
                    "INSERT INTO sessions (seed_name, team, slot, slot_name, server, catalogue_fingerprint, "
                    "world_version, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (seed_name, team, slot, slot_name, server, catalogue_fingerprint, world_version, now()),
                )
            c.execute("UPDATE sessions SET last_connected_at=?, server=? WHERE seed_name=? AND team=? AND slot=?",
                      (now(), server, seed_name, team, slot))
            row = c.execute("SELECT * FROM sessions WHERE seed_name=? AND team=? AND slot=?",
                            (seed_name, team, slot)).fetchone()
        return self._session(row)

    def session(self, session_id: int) -> Session:
        row = self._one("SELECT * FROM sessions WHERE id=?", (session_id,))
        if row is None:
            raise LedgerError(f"no session {session_id}")
        return self._session(row)

    def sessions_for_profile(self, profile_id: str) -> list[Session]:
        return [self._session(r) for r in self._all("SELECT * FROM sessions WHERE profile_id=? ORDER BY id", (profile_id,))]

    def latest_session(self) -> Session | None:
        row = self._one("SELECT * FROM sessions ORDER BY last_connected_at DESC, id DESC LIMIT 1")
        return self._session(row) if row else None

    def bind_profile(self, session_id: int, profile_id: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE sessions SET profile_id=? WHERE id=? AND profile_id IS NULL", (profile_id, session_id))

    def set_goal(self, session_id: int, *, detected: bool | None = None, reported: bool | None = None) -> None:
        with self.tx() as c:
            if detected is not None:
                c.execute("UPDATE sessions SET goal_detected=? WHERE id=?", (int(detected), session_id))
            if reported is not None:
                c.execute("UPDATE sessions SET goal_reported=? WHERE id=?", (int(reported), session_id))

    @staticmethod
    def _session(row: sqlite3.Row) -> Session:
        return Session(row["id"], row["seed_name"], row["team"], row["slot"], row["slot_name"], row["server"],
                       row["profile_id"], row["catalogue_fingerprint"], row["world_version"],
                       bool(row["goal_detected"]), bool(row["goal_reported"]), row["created_at"])

    # ------------------------------------------------------------------ received items
    def next_index(self, session_id: int) -> int:
        row = self._one("SELECT COALESCE(MAX(idx) + 1, 0) AS n FROM received_items WHERE session_id=?",
                                (session_id,))
        return int(row["n"])

    def received(self, session_id: int, idx: int) -> ReceivedItem | None:
        row = self._one("SELECT * FROM received_items WHERE session_id=? AND idx=?",
                                (session_id, idx))
        return self._item(row) if row else None

    def record_received(self, session_id: int, idx: int, item_id: int, location_id: int, sender: int,
                        flags: int) -> bool:
        """Insert one received item. Returns False if the index was already known (a replay)."""
        with self.tx() as c:
            existing = c.execute("SELECT item_id, location_id, sender FROM received_items WHERE session_id=? AND idx=?",
                                 (session_id, idx)).fetchone()
            if existing is not None:
                if (existing["item_id"], existing["location_id"], existing["sender"]) != (item_id, location_id, sender):
                    raise LedgerError(
                        f"server sent a different item at index {idx} than before: this ledger does not belong to "
                        f"this multiworld session"
                    )
                return False
            ts = now()
            c.execute(
                "INSERT INTO received_items (session_id, idx, item_id, location_id, sender, flags, state, "
                "received_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (session_id, idx, item_id, location_id, sender, flags, DeliveryState.RECEIVED.value, ts, ts),
            )
            return True

    def items(self, session_id: int, states: tuple[DeliveryState, ...] | None = None) -> list[ReceivedItem]:
        if states:
            marks = ",".join("?" * len(states))
            rows = self._all(
                f"SELECT * FROM received_items WHERE session_id=? AND state IN ({marks}) ORDER BY idx",
                (session_id, *[s.value for s in states]),
            )
        else:
            rows = self._all("SELECT * FROM received_items WHERE session_id=? ORDER BY idx",
                                     (session_id,))
        return [self._item(r) for r in rows]

    def set_item_state(self, session_id: int, indices: list[int], state: DeliveryState, *,
                       error: str | None = None, batch_id: str | None = None, bump_attempts: bool = False,
                       expect: tuple[DeliveryState, ...] | None = None) -> None:
        if not indices:
            return
        with self.tx() as c:
            for idx in indices:
                row = c.execute("SELECT state FROM received_items WHERE session_id=? AND idx=?",
                                (session_id, idx)).fetchone()
                if row is None:
                    raise LedgerError(f"no received item {idx}")
                if expect and DeliveryState(row["state"]) not in expect:
                    raise LedgerError(f"item {idx} is {row['state']}, expected one of {[e.value for e in expect]}")
                c.execute(
                    "UPDATE received_items SET state=?, last_error=?, batch_id=COALESCE(?, batch_id), "
                    "attempts=attempts+?, updated_at=? WHERE session_id=? AND idx=?",
                    (state.value, error, batch_id, 1 if bump_attempts else 0, now(), session_id, idx),
                )

    def state_counts(self, session_id: int) -> dict[str, int]:
        rows = self._all("SELECT state, COUNT(*) AS n FROM received_items WHERE session_id=? GROUP BY state",
                                 (session_id,))
        return {r["state"]: r["n"] for r in rows}

    @staticmethod
    def _item(row: sqlite3.Row) -> ReceivedItem:
        return ReceivedItem(row["session_id"], row["idx"], row["item_id"], row["location_id"], row["sender"],
                            row["flags"], DeliveryState(row["state"]), row["batch_id"], row["attempts"],
                            row["last_error"])

    # ------------------------------------------------------------------ delivery batches
    def begin_batch(self, session_id: int, batch_id: str, indices: list[int], pre_fp: str, post_fp: str,
                    temp_path: str | None) -> None:
        """Durably record an in-flight write and move its items to APPLYING, in one transaction."""
        with self.tx() as c:
            ts = now()
            c.execute(
                "INSERT INTO delivery_batches VALUES (?,?,?,?,?,?,?,?)",
                (batch_id, session_id, BatchState.PREPARED.value, pre_fp, post_fp, temp_path, ts, ts),
            )
            for idx in indices:
                updated = c.execute(
                    "UPDATE received_items SET state=?, batch_id=?, attempts=attempts+1, updated_at=? "
                    "WHERE session_id=? AND idx=? AND state=?",
                    (DeliveryState.APPLYING.value, batch_id, ts, session_id, idx, DeliveryState.PENDING.value),
                ).rowcount
                if updated != 1:
                    raise LedgerError(f"item {idx} was not PENDING when its delivery started")

    def finish_batch(self, batch_id: str, batch_state: BatchState, item_state: DeliveryState,
                     error: str | None = None) -> None:
        with self.tx() as c:
            ts = now()
            c.execute("UPDATE delivery_batches SET state=?, updated_at=? WHERE batch_id=?",
                      (batch_state.value, ts, batch_id))
            c.execute("UPDATE received_items SET state=?, last_error=?, updated_at=? WHERE batch_id=? AND state=?",
                      (item_state.value, error, ts, batch_id, DeliveryState.APPLYING.value))

    def batches(self, session_id: int, states: tuple[BatchState, ...]) -> list[Batch]:
        marks = ",".join("?" * len(states))
        rows = self._all(
            f"SELECT * FROM delivery_batches WHERE session_id=? AND state IN ({marks}) ORDER BY created_at",
            (session_id, *[s.value for s in states]),
        )
        return [Batch(r["batch_id"], r["session_id"], BatchState(r["state"]), r["pre_fingerprint"],
                      r["post_fingerprint"], r["temp_path"]) for r in rows]

    def batch_items(self, batch_id: str) -> list[ReceivedItem]:
        rows = self._all("SELECT * FROM received_items WHERE batch_id=? ORDER BY idx", (batch_id,))
        return [self._item(r) for r in rows]

    # ------------------------------------------------------------------ checks
    def record_checks(self, session_id: int, location_ids: set[int]) -> set[int]:
        """Record detected checks; returns the ones that were new."""
        new: set[int] = set()
        with self.tx() as c:
            for loc in sorted(location_ids):
                cur = c.execute(
                    "INSERT INTO checks (session_id, location_id, detected_at) VALUES (?,?,?) "
                    "ON CONFLICT (session_id, location_id) DO UPDATE SET "
                    "detected_at=COALESCE(checks.detected_at, excluded.detected_at) "
                    "WHERE checks.detected_at IS NULL",
                    (session_id, loc, now()),
                )
                if cur.rowcount:
                    new.add(loc)
        return new

    def unconfirmed_checks(self, session_id: int) -> set[int]:
        rows = self._all(
            "SELECT location_id FROM checks WHERE session_id=? AND detected_at IS NOT NULL AND confirmed_at IS NULL",
            (session_id,))
        return {r["location_id"] for r in rows}

    def mark_checks_sent(self, session_id: int, location_ids: set[int]) -> None:
        with self.tx() as c:
            for loc in location_ids:
                c.execute("UPDATE checks SET sent_at=COALESCE(sent_at, ?) WHERE session_id=? AND location_id=?",
                          (now(), session_id, loc))

    def confirm_checks(self, session_id: int, location_ids: set[int]) -> None:
        """Server says these are checked (by us, or by collect/release)."""
        with self.tx() as c:
            ts = now()
            for loc in location_ids:
                c.execute(
                    "INSERT INTO checks (session_id, location_id, confirmed_at) VALUES (?,?,?) "
                    "ON CONFLICT (session_id, location_id) DO UPDATE SET "
                    "confirmed_at=COALESCE(checks.confirmed_at, excluded.confirmed_at)",
                    (session_id, loc, ts),
                )

    def check_counts(self, session_id: int) -> dict[str, int]:
        row = self._one(
            "SELECT COUNT(detected_at) AS detected, COUNT(sent_at) AS sent, COUNT(confirmed_at) AS confirmed "
            "FROM checks WHERE session_id=?", (session_id,))
        return dict(row)

    # ------------------------------------------------------------------ events
    def log_event(self, session_id: int | None, kind: str, message: str) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO events (session_id, at, kind, message) VALUES (?,?,?,?)",
                      (session_id, now(), kind, message))

    def recent_events(self, limit: int = 100) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
