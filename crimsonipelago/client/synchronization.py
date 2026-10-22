"""The synchronization controller: glues the server, the ledger and the game together.

Responsibilities (scope doc 5):
  * handshake and session identity validation (seed, slot, game, catalogue, save profile)
  * recording ReceivedItems by index, with replay / gap / full-resync handling
  * sending checks (including ones earned while offline) and the goal
  * driving reward delivery through the DeliveryEngine
  * pausing with an actionable message whenever continuing could cross seeds or corrupt state
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crimson_desert.catalogue import Catalogue

from ..game_adapter.interface import GameAdapter
from . import events as ev
from . import protocol
from .ap_connection import APConnection
from .check_tracker import CheckTracker
from .database import DeliveryState, Ledger, LedgerError, Session
from .datapackage import DataPackageCache
from .item_delivery import DeliveryEngine

log = logging.getLogger("crimsonipelago.sync")

FATAL_REFUSALS = {"InvalidSlot", "InvalidGame", "InvalidPassword", "IncompatibleVersion"}
RESEND_UNCONFIRMED_EVERY = 30.0


@dataclass
class ClientConfig:
    server: str = "localhost"
    port: int | None = None
    slot_name: str = ""
    password: str = ""
    data_dir: Path = field(default_factory=lambda: Path("crimsonipelago_data"))
    poll_interval: float = 2.0
    client_uuid: str = ""


@dataclass
class StatusSnapshot:
    connection: str = "disconnected"
    connection_detail: str = ""
    server: str = ""
    slot_name: str = ""
    seed_name: str | None = None
    game_version: str = ""
    game_supported: bool = False
    writes_allowed: bool = False
    profile_id: str | None = None
    catalogue_status: str = ""
    paused: str | None = None
    pending_deliveries: int = 0
    waiting_reason: str | None = None
    unsupported_items: int = 0
    recovery_required: int = 0
    delivered: int = 0
    checks_detected: int = 0
    checks_confirmed: int = 0
    goal_detected: bool = False
    goal_reported: bool = False


class SyncController:
    def __init__(self, config: ClientConfig, adapter: GameAdapter, ledger: Ledger, catalogue: Catalogue,
                 bus: ev.EventBus | None = None):
        self.config, self.adapter, self.ledger, self.catalogue = config, adapter, ledger, catalogue
        self.bus = bus or ev.EventBus()
        self.tracker = CheckTracker(ledger, adapter, catalogue, self.bus)
        self.engine = DeliveryEngine(ledger, adapter, catalogue, self.bus)
        self.names = DataPackageCache(Path(config.data_dir) / "datapackage")
        self.connection: APConnection | None = None
        self._adapter_lock = asyncio.Lock()
        # Packet handlers, ticks and user actions all read and replace self.session across awaits.
        # Running them one at a time keeps a tick that started under one seed from undoing a switch to another.
        self._state_lock = asyncio.Lock()
        self._stopping = asyncio.Event()

        self.room_info: dict[str, Any] | None = None
        self.authenticated = False
        self.session: Session | None = None
        self.slot_data: dict[str, Any] = {}
        self.team: int | None = None
        self.slot: int | None = None
        self.server_checked: set[int] = set()
        self.enabled_location_ids: set[int] | None = None
        self.goal_key = catalogue.default_goal
        self.paused: str | None = None
        self.identity_error: str | None = None  # pauses that only a reconnect / user action can clear
        self.status = StatusSnapshot(server=config.server, slot_name=config.slot_name,
                                     catalogue_status=catalogue.status)
        self._last_resend = 0.0
        if not config.client_uuid:
            config.client_uuid = self._load_uuid()

    # ------------------------------------------------------------------ setup
    def _load_uuid(self) -> str:
        path = Path(self.config.data_dir) / "client_uuid"
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            value = uuid.uuid4().hex
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")
            return value

    async def _adapter(self, fn, *args):
        async with self._adapter_lock:
            return await asyncio.to_thread(fn, *args)

    async def startup(self) -> None:
        """Resume the last session offline (if it matches the loaded save) and finish interrupted writes."""
        version = await self._adapter(self.adapter.get_game_version)
        self.status.game_version, self.status.game_supported = version.label, version.supported
        setup_warnings = getattr(self.adapter, "setup_warnings", None)
        if setup_warnings:
            for warning in await self._adapter(setup_warnings):
                self.bus.emit(ev.ERROR, warning)
        self.status.writes_allowed = version.writes_allowed
        profile = await self._adapter(self.adapter.get_current_profile)
        self.status.profile_id = profile
        latest = self.ledger.latest_session()
        if latest and profile and latest.profile_id == profile:
            self.session = latest
            self.status.seed_name = latest.seed_name
            self.bus.emit(ev.CONNECTION, f"Resuming seed {latest.seed_name} offline until the server is reachable")
        if self.session:
            await self._adapter(self.engine.recover, self.session.id)
            await self._adapter(self.engine.revalidate_unsupported, self.session.id)

    # ------------------------------------------------------------------ run loop
    async def run(self) -> None:
        await self.startup()
        self.connection = APConnection(self.config.server, self.config.port, self.on_packet, self.on_connection_state)
        conn_task = asyncio.create_task(self.connection.run())
        try:
            while not self._stopping.is_set() and not conn_task.done():
                try:
                    async with self._state_lock:
                        await self.tick()
                except Exception as exc:  # never let one bad tick kill synchronization
                    log.exception("sync tick failed")
                    self.bus.emit(ev.ERROR, f"Synchronization error: {exc}")
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=self.config.poll_interval)
                except TimeoutError:
                    pass
        finally:
            self.connection.stop()
            (result,) = await asyncio.gather(conn_task, return_exceptions=True)
            if isinstance(result, Exception):
                log.error("connection task crashed", exc_info=result)
                self.bus.emit(ev.ERROR, f"The connection to Archipelago stopped unexpectedly: {result}")

    def stop(self) -> None:
        self._stopping.set()
        if self.connection:
            self.connection.stop()

    async def on_connection_state(self, state: str, detail: str) -> None:
        if state in ("disconnected", "stopped"):
            if self.authenticated:
                self.bus.emit(ev.CONNECTION, "Disconnected from the Archipelago server; progress is kept locally")
            self.authenticated = False
            self.room_info = None
            if detail and detail != self.status.connection_detail:
                self.bus.emit(ev.CONNECTION, detail)
        self.status.connection, self.status.connection_detail = state, detail
        self._emit_status()

    async def send(self, *commands: dict) -> bool:
        return bool(self.connection and await self.connection.send(commands))

    # ------------------------------------------------------------------ packets
    async def on_packet(self, cmd: dict[str, Any]) -> None:
        handler = getattr(self, f"_on_{cmd.get('cmd')}", None)
        if handler is None:
            return
        try:
            async with self._state_lock:
                await handler(cmd)
        except Exception as exc:
            # Never let one bad packet silently end synchronization. Dropping the socket makes the
            # client reconnect, and the server then resends Connected and the full item list, which
            # the ledger reconciles without duplicates.
            log.exception("handling %s failed", cmd.get("cmd"))
            self.bus.emit(ev.ERROR, f"Synchronization error while handling {cmd.get('cmd')}: {exc}. Reconnecting.")
            if self.connection:
                self.connection.reconnect()

    async def _on_RoomInfo(self, cmd: dict) -> None:
        self.room_info = cmd
        checksums = cmd.get("datapackage_checksums") or {}
        missing = self.names.missing_games(checksums) if checksums else list(cmd.get("games", []))
        if missing:
            await self.send(protocol.get_data_package(missing))
        await self.send(protocol.connect(self.config.slot_name, self.config.password, self.config.client_uuid))

    async def _on_DataPackage(self, cmd: dict) -> None:
        self.names.store(cmd.get("data", {}).get("games", {}))

    async def _on_ConnectionRefused(self, cmd: dict) -> None:
        errors = cmd.get("errors", [])
        message = protocol.explain_refusal(errors)
        self.bus.emit(ev.ERROR, f"Connection refused: {message}")
        if FATAL_REFUSALS & set(errors) and self.connection:
            self.connection.fail(message)

    async def _on_Connected(self, cmd: dict) -> None:
        seed = (self.room_info or {}).get("seed_name")
        self.team, self.slot = int(cmd["team"]), int(cmd["slot"])
        slot_info = cmd.get("slot_info", {})
        self.names.set_players(cmd.get("players", []), slot_info, self.team)
        self.slot_data = cmd.get("slot_data") or {}
        game = slot_info.get(str(self.slot), {}).get("game")

        problem = None
        if not seed:
            problem = "The server did not report a seed name; refusing to sync without a session identity."
        elif game != protocol.GAME:
            problem = f"Slot {self.config.slot_name!r} is a {game} slot, not Crimson Desert."
        elif self.slot_data.get("catalogue_fingerprint") != self.catalogue.fingerprint:
            problem = (
                "This seed was generated with a different Crimson Desert APWorld than this client was built with "
                f"(seed catalogue {self.slot_data.get('catalogue_fingerprint')}, client {self.catalogue.fingerprint}). "
                "Install the client release that matches the APWorld used for generation."
            )
        if problem:
            self._pause(problem, identity=True)
            if self.connection:
                self.connection.fail(problem)
            return

        session = self.ledger.get_or_create_session(
            seed, self.team, self.slot, self.config.slot_name, self.config.server,
            self.slot_data.get("catalogue_fingerprint"), self.slot_data.get("world_version"))
        if self.session and self.session.id != session.id:
            self.bus.emit(ev.CONNECTION, f"Switched from seed {self.session.seed_name} to {seed}")
        self.session = session
        self.goal_key = self.slot_data.get("goal", self.catalogue.default_goal)
        categories = set(self.slot_data.get("included_check_categories", []))
        self.enabled_location_ids = {loc.ap_id for loc in self.catalogue.locations.values()
                                     if not categories or loc.category in categories}
        self.authenticated = True
        self.identity_error = None
        self.status.seed_name = seed
        self.status.connection = "connected"

        await self._check_profile()
        checked = {int(x) for x in cmd.get("checked_locations", [])}
        self.server_checked = checked
        self.ledger.confirm_checks(session.id, checked)
        self.bus.emit(ev.CONNECTION, f"Connected to {self.config.server} as {self.config.slot_name} (seed {seed})")
        await self._flush_checks(force=True)
        await self._flush_goal()
        await self.send(protocol.status_update(protocol.ClientStatus.PLAYING))

    async def _on_ReceivedItems(self, cmd: dict) -> None:
        if not self.session:
            return
        sid = self.session.id
        index = int(cmd["index"])
        items = [protocol.NetworkItem.from_json(i) for i in cmd.get("items", [])]
        expected = self.ledger.next_index(sid)
        if index > expected:
            # a gap: ask for everything again rather than storing a hole in the ledger
            await self.send(protocol.sync(), protocol.location_checks(self.ledger.unconfirmed_checks(sid)))
            return
        new = []
        try:
            for offset, item in enumerate(items):
                if self.ledger.record_received(sid, index + offset, item.item, item.location, item.player, item.flags):
                    new.append(item)
        except LedgerError as exc:
            self._pause(str(exc), identity=True)
            return
        for item in new:
            name = self.catalogue.item_by_id(item.item)
            sender = "your own world" if item.player == self.slot else (
                "starting inventory" if item.player == 0 else self.names.player_name(item.player))
            self.bus.emit(ev.ITEM_RECEIVED, f"Received {name.name if name else item.item} from {sender}",
                          item_id=item.item, sender=item.player)
        if new:
            await self._deliver()

    async def _on_RoomUpdate(self, cmd: dict) -> None:
        if self.session and "checked_locations" in cmd:
            checked = {int(x) for x in cmd["checked_locations"]}
            self.server_checked |= checked
            self.ledger.confirm_checks(self.session.id, checked)
        if "players" in cmd and self.team is not None:
            self.names.set_players(cmd["players"], {str(k): {"game": v} for k, v in self.names.slot_games.items()},
                                   self.team)

    async def _on_PrintJSON(self, cmd: dict) -> None:
        kind = cmd.get("type")
        text = protocol.render_print_json(cmd.get("data", []), self.names)
        if kind in ("ItemSend", "ItemCheat"):
            item = cmd.get("item") or {}
            receiving = cmd.get("receiving")
            if int(item.get("player", -1)) == self.slot and receiving != self.slot:
                self.bus.emit(ev.ITEM_SENT, text)
        elif kind in ("Chat", "ServerChat", "Goal", "Release", "Collect", "Countdown", "Hint", None):
            self.bus.emit(ev.CHAT, text)

    # ------------------------------------------------------------------ periodic work
    async def tick(self) -> None:
        await self._check_profile()
        if self.session and not self.paused:
            sid = self.session.id
            new = await self._adapter(self.tracker.poll, sid, self.enabled_location_ids)
            await self._adapter(self.tracker.poll_goal, sid, self.goal_key)
            if not self.session or self.session.id != sid:
                return  # the session changed while this tick was waiting; never switch it back
            self.session = self.ledger.session(sid)
            if self.authenticated:
                await self._flush_checks(force=bool(new))
                await self._flush_goal()
            await self._deliver()
        self._refresh_status()

    async def _check_profile(self) -> None:
        profile = await self._adapter(self.adapter.get_current_profile)
        self.status.profile_id = profile
        if self.identity_error:
            return
        if not self.session:
            return
        if profile is None:
            problem = getattr(self.adapter, "identity_problem", None)
            self._pause(f"Waiting for the campaign: {problem}." if problem else
                        "No Crimson Desert save was found. Start or load the campaign for this seed.")
            return
        session = self.ledger.session(self.session.id)
        if session.profile_id is None:
            other = [s for s in self.ledger.sessions_for_profile(profile) if s.id != session.id]
            if other:
                self._pause(f"This playthrough ({profile}) already belongs to seed {other[0].seed_name}. "
                            f"Start a new game for this seed.")
                return
            self.ledger.bind_profile(session.id, profile)
            self.bus.emit(ev.CONNECTION, f"Seed {session.seed_name} is now bound to playthrough {profile}")
            session = self.ledger.session(session.id)
        if session.profile_id != profile:
            self._pause(f"This seed belongs to playthrough {session.profile_id}, but the newest save is from "
                        f"{profile}. Load a save of the right playthrough (and save once) to continue.")
            return
        self.session = session
        self._resume()

    async def _flush_checks(self, *, force: bool = False) -> None:
        if not (self.session and self.authenticated) or self.paused:
            return
        loop = asyncio.get_running_loop()
        if not force and loop.time() - self._last_resend < RESEND_UNCONFIRMED_EVERY:
            return
        pending = self.ledger.unconfirmed_checks(self.session.id) - self.server_checked
        if not pending:
            return
        if await self.send(protocol.location_checks(pending)):
            self._last_resend = loop.time()
            self.ledger.mark_checks_sent(self.session.id, pending)
            for loc_id in sorted(pending):
                loc = self.catalogue.location_by_id(loc_id)
                self.bus.emit(ev.CHECK_SENT, f"Sent check: {loc.name if loc else loc_id}", location_id=loc_id)

    async def _flush_goal(self) -> None:
        if not (self.session and self.authenticated) or self.paused:
            return
        session = self.ledger.session(self.session.id)
        if session.goal_detected and not session.goal_reported:
            if await self.send(protocol.status_update(protocol.ClientStatus.GOAL)):
                self.ledger.set_goal(session.id, reported=True)
                self.bus.emit(ev.GOAL, "Victory reported to the Archipelago server")

    async def _deliver(self) -> None:
        if not self.session or self.paused:
            return
        sid = self.session.id
        await self._adapter(self.engine.validate, sid)
        await self._adapter(self.engine.reconcile, sid)
        report = await self._adapter(self.engine.deliver_pending, sid)
        self.status.waiting_reason = report.waiting_reason

    async def resolve_recovery(self, batch_id: str, decision: str) -> None:
        async with self._state_lock:
            if not self.session:
                raise ValueError("no active session")
            await self._adapter(self.engine.resolve_recovery, self.session.id, batch_id, decision)
            await self._deliver()

    # ------------------------------------------------------------------ pause / status
    def _pause(self, reason: str, *, identity: bool = False) -> None:
        if identity:
            self.identity_error = reason
        if self.paused != reason:
            self.paused = reason
            self.bus.emit(ev.SYNC_PAUSED, f"Synchronization paused: {reason}")
        self._refresh_status()

    def _resume(self) -> None:
        if self.paused and not self.identity_error:
            self.paused = None
            self.bus.emit(ev.SYNC_RESTORED, "Synchronization restored")

    def _refresh_status(self) -> None:
        s = self.status
        s.paused = self.paused
        if self.session:
            counts = self.ledger.state_counts(self.session.id)
            s.pending_deliveries = sum(counts.get(st.value, 0) for st in
                                       (DeliveryState.RECEIVED, DeliveryState.VALIDATED, DeliveryState.PENDING,
                                        DeliveryState.APPLYING))
            s.unsupported_items = counts.get(DeliveryState.UNSUPPORTED.value, 0)
            s.recovery_required = counts.get(DeliveryState.RECOVERY_REQUIRED.value, 0)
            s.delivered = counts.get(DeliveryState.COMPLETE.value, 0)
            checks = self.ledger.check_counts(self.session.id)
            s.checks_detected, s.checks_confirmed = checks["detected"], checks["confirmed"]
            session = self.ledger.session(self.session.id)
            s.goal_detected, s.goal_reported = session.goal_detected, session.goal_reported
        self._emit_status()

    def _emit_status(self) -> None:
        self.bus.emit(ev.STATUS, "", status=self.status)
