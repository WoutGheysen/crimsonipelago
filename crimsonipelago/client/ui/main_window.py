"""Crimsonipelago desktop client (scope doc section 8), built with PySide6.

The synchronization controller runs on its own asyncio loop in a background
thread. Everything it reports reaches the window through the EventBus, which
is bridged into Qt with a queued signal, so the UI thread never blocks on the
network, the save file or the ledger.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

from crimson_desert.catalogue import Catalogue
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ... import __version__
from .. import events as ev
from ..database import BatchState, Ledger
from ..synchronization import ClientConfig, StatusSnapshot, SyncController

ACCENT = "#b3261e"  # crimson

FEED_STYLE: dict[str, tuple[str, str]] = {  # kind -> (tag, colour)
    ev.CHECK_SENT: ("CHECK", "#2e7d32"),
    ev.CHECK_DETECTED: ("FOUND", "#558b2f"),
    ev.ITEM_RECEIVED: ("RECV", "#1565c0"),
    ev.ITEM_SENT: ("SENT", "#6a1b9a"),
    ev.REWARD_DELIVERED: ("GIVEN", "#00838f"),
    ev.REWARD_PENDING: ("WAIT", "#ef6c00"),
    ev.REWARD_UNSUPPORTED: ("HELD", "#8d6e63"),
    ev.RECOVERY_REQUIRED: ("CHECK!", "#c62828"),
    ev.CONNECTION: ("NET", "#546e7a"),
    ev.SYNC_PAUSED: ("PAUSE", "#c62828"),
    ev.SYNC_RESTORED: ("OK", "#2e7d32"),
    ev.GOAL: ("GOAL", ACCENT),
    ev.CHAT: ("ROOM", "#78909c"),
    ev.ERROR: ("ERROR", "#c62828"),
}

CONNECTION_LABELS = {
    "disconnected": ("Not connected", "#9e9e9e"),
    "connecting": ("Connecting…", "#ef6c00"),
    "socket_open": ("Authenticating…", "#ef6c00"),
    "connected": ("Connected", "#2e7d32"),
    "stopped": ("Stopped", "#9e9e9e"),
}

STYLE = f"""
QMainWindow, QWidget#root {{ background: palette(window); }}
QGroupBox {{ font-weight: 600; border: 1px solid palette(mid); border-radius: 6px; margin-top: 14px; padding: 10px 8px 8px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
QPushButton#primary {{ background: {ACCENT}; color: white; border: none; border-radius: 4px; padding: 6px 18px; font-weight: 600; }}
QPushButton#primary:disabled {{ background: #9e9e9e; }}
QFrame#banner {{ border-radius: 6px; padding: 8px; }}
QLabel.value {{ font-weight: 600; }}
QListWidget {{ font-family: Consolas, "DejaVu Sans Mono", monospace; font-size: 12px; border-radius: 6px; }}
"""


def app_icon() -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(ACCENT))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(4, 4, 56, 56, 12, 12)
    p.setPen(QColor("white"))
    f = QFont()
    f.setBold(True)
    f.setPixelSize(30)
    p.setFont(f)
    p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "CA")
    p.end()
    return QIcon(pix)


class Bridge(QObject):
    event = Signal(object)  # ev.Event, delivered on the UI thread


class ControllerThread:
    """Owns the asyncio loop the SyncController runs on."""

    def __init__(self, controller: SyncController, ledger: Ledger):
        self.controller, self.ledger = controller, ledger
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name="crimsonipelago-sync", daemon=True)
        self.finished = threading.Event()

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self.controller.run())
        except Exception as exc:  # surface instead of dying silently
            self.controller.bus.emit(ev.ERROR, f"The client stopped unexpectedly: {exc}")
        finally:
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            self.loop.close()
            self.ledger.close()
            self.finished.set()

    def call(self, coro, timeout: float = 15.0) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def stop(self, timeout: float = 10.0) -> None:
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.controller.stop)
        self.finished.wait(timeout)


def _value_label(text: str = "—") -> QLabel:
    label = QLabel(text)
    label.setProperty("class", "value")
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setWordWrap(True)
    return label


class MainWindow(QMainWindow):
    def __init__(self, data_dir: Path, catalogue: Catalogue, adapter_kind: str = "save",
                 adapter_options: dict[str, Any] | None = None, settings: dict[str, Any] | None = None):
        super().__init__()
        from ..main import build_adapter, load_settings  # local import: main imports this module lazily

        self._build_adapter = build_adapter
        self.data_dir, self.catalogue = Path(data_dir), catalogue
        self.settings = settings if settings is not None else load_settings(self.data_dir)
        opts = adapter_options or {}
        self.adapter_kind = self.settings.get("adapter", adapter_kind) if adapter_kind == "save" else adapter_kind
        self.adapter_options = {
            "save_path": opts.get("save_path") or self.settings.get("save_path"),
            "editor_dir": opts.get("editor_dir") or self.settings.get("editor_dir"),
            "steam_manifest": opts.get("steam_manifest") or self.settings.get("steam_manifest"),
            "sim_file": opts.get("sim_file") or self.settings.get("sim_file"),
        }
        self.worker: ControllerThread | None = None
        self.bridge = Bridge()
        self.bridge.event.connect(self._on_event)
        self.recovery_batches: list[str] = []

        self.setWindowTitle(f"Crimsonipelago {__version__}")
        self.setWindowIcon(app_icon())
        self.resize(980, 720)
        self.setStyleSheet(STYLE)
        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 10, 14, 14)
        layout.setSpacing(10)

        layout.addWidget(self._header())
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(self._connection_box(), 3)
        top.addWidget(self._game_box(), 4)
        layout.addLayout(top)
        layout.addWidget(self._banner())
        layout.addWidget(self._recovery_bar())
        layout.addWidget(self._status_box())
        layout.addWidget(self._feed_box(), 1)

        self._apply_status(StatusSnapshot(server=self.server_edit.text(), slot_name=self.slot_edit.text(),
                                          catalogue_status=catalogue.status))
        self._sync_adapter_fields()

    # ------------------------------------------------------------------ layout
    def _header(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Crimsonipelago")
        f = title.font()
        f.setPointSize(f.pointSize() + 6)
        f.setBold(True)
        title.setFont(f)
        title.setStyleSheet(f"color: {ACCENT};")
        sub = QLabel("Crimson Desert × Archipelago")
        sub.setStyleSheet("color: palette(mid);")
        self.conn_pill = QLabel()
        self.conn_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.conn_pill.setMinimumWidth(150)
        h.addWidget(title)
        h.addSpacing(8)
        h.addWidget(sub)
        h.addStretch(1)
        h.addWidget(self.conn_pill)
        return w

    def _connection_box(self) -> QGroupBox:
        box = QGroupBox("Archipelago server")
        form = QFormLayout(box)
        self.server_edit = QLineEdit(self.settings.get("server", "archipelago.gg"))
        self.server_edit.setPlaceholderText("archipelago.gg or ws://localhost")
        self.port_spin = QSpinBox()
        self.port_spin.setRange(0, 65535)
        self.port_spin.setSpecialValueText("from address")
        self.port_spin.setValue(int(self.settings.get("port") or 0))
        self.slot_edit = QLineEdit(self.settings.get("slot_name", ""))
        self.slot_edit.setPlaceholderText("your player name in the YAML")
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("leave empty if the room has none")
        form.addRow("Address", self.server_edit)
        form.addRow("Port", self.port_spin)
        form.addRow("Player name", self.slot_edit)
        form.addRow("Password", self.password_edit)
        buttons = QHBoxLayout()
        self.connect_btn = QPushButton("Connect", objectName="primary")
        self.connect_btn.clicked.connect(self.connect_clicked)
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(self.disconnect_clicked)
        self.disconnect_btn.setEnabled(False)
        buttons.addStretch(1)
        buttons.addWidget(self.disconnect_btn)
        buttons.addWidget(self.connect_btn)
        form.addRow(buttons)
        return box

    def _path_row(self, edit: QLineEdit, pick_dir: bool, caption: str, file_filter: str = "") -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        btn = QPushButton("Browse…")

        def pick():
            if pick_dir:
                path = QFileDialog.getExistingDirectory(self, caption, edit.text() or str(Path.home()))
            else:
                path, _ = QFileDialog.getOpenFileName(self, caption, edit.text() or str(Path.home()), file_filter)
            if path:
                edit.setText(path)

        btn.clicked.connect(pick)
        h.addWidget(edit, 1)
        h.addWidget(btn)
        return w

    def _game_box(self) -> QGroupBox:
        box = QGroupBox("Crimson Desert")
        form = QFormLayout(box)
        self.adapter_combo = QComboBox()
        self.adapter_combo.addItem("Save files (real game)", "save")
        self.adapter_combo.addItem("Simulated game (testing)", "sim")
        self.adapter_combo.setCurrentIndex(max(0, self.adapter_combo.findData(self.adapter_kind)))
        self.adapter_combo.currentIndexChanged.connect(self._sync_adapter_fields)
        self.save_edit = QLineEdit(self.adapter_options.get("save_path") or "")
        self.save_edit.setPlaceholderText("automatic: newest save.save")
        self.editor_edit = QLineEdit(self.adapter_options.get("editor_dir") or "")
        self.editor_edit.setPlaceholderText("CrimsonSaveEditor folder (enables check detection)")
        self.sim_edit = QLineEdit(self.adapter_options.get("sim_file") or "")
        self.sim_edit.setPlaceholderText(str(self.data_dir / "simulated_game.json"))
        form.addRow("Game source", self.adapter_combo)
        self.save_row = self._path_row(self.save_edit, False, "Choose save.save", "Crimson Desert save (*.save)")
        self.editor_row = self._path_row(self.editor_edit, True, "Choose your CrimsonSaveEditor folder")
        self.sim_row = self._path_row(self.sim_edit, False, "Choose simulated game file", "JSON (*.json)")
        form.addRow("Save file", self.save_row)
        form.addRow("Save editor", self.editor_row)
        form.addRow("Sim file", self.sim_row)
        self.game_form = form
        return box

    def _sync_adapter_fields(self) -> None:
        sim = self.adapter_combo.currentData() == "sim"
        for row, visible in ((self.save_row, not sim), (self.editor_row, not sim), (self.sim_row, sim)):
            row.setVisible(visible)
            label = self.game_form.labelForField(row)
            if label:
                label.setVisible(visible)

    def _banner(self) -> QFrame:
        self.banner = QFrame(objectName="banner")
        h = QHBoxLayout(self.banner)
        h.setContentsMargins(10, 6, 10, 6)
        self.banner_label = QLabel()
        self.banner_label.setWordWrap(True)
        h.addWidget(self.banner_label, 1)
        self.banner.hide()
        return self.banner

    def _recovery_bar(self) -> QFrame:
        self.recovery = QFrame(objectName="banner")
        self.recovery.setStyleSheet("QFrame#banner { background: #ffebee; color: #b71c1c; }")
        h = QHBoxLayout(self.recovery)
        h.setContentsMargins(10, 6, 10, 6)
        self.recovery_label = QLabel()
        self.recovery_label.setWordWrap(True)
        self.recovery_delivered = QPushButton("It's in my inventory")
        self.recovery_retry = QPushButton("It's missing: deliver again")
        self.recovery_delivered.clicked.connect(lambda: self.resolve_recovery("delivered"))
        self.recovery_retry.clicked.connect(lambda: self.resolve_recovery("retry"))
        h.addWidget(self.recovery_label, 1)
        h.addWidget(self.recovery_delivered)
        h.addWidget(self.recovery_retry)
        self.recovery.hide()
        return self.recovery

    def _status_box(self) -> QGroupBox:
        box = QGroupBox("Status")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        self.fields: dict[str, QLabel] = {}
        rows = [
            [("seed", "Seed"), ("game", "Game version"), ("profile", "Save profile")],
            [("checks", "Checks sent"), ("rewards", "Rewards delivered"), ("pending", "Waiting")],
            [("catalogue", "Game data"), ("held", "Held back"), ("goal", "Goal")],
        ]
        for r, row in enumerate(rows):
            for c, (key, title) in enumerate(row):
                cap = QLabel(title)
                cap.setStyleSheet("color: palette(mid);")
                val = _value_label()
                val.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                self.fields[key] = val
                grid.addWidget(cap, r, c * 2)
                grid.addWidget(val, r, c * 2 + 1)
        for c in (1, 3, 5):
            grid.setColumnStretch(c, 1)
        return box

    def _feed_box(self) -> QGroupBox:
        box = QGroupBox("Activity")
        v = QVBoxLayout(box)
        self.feed = QListWidget()
        self.feed.setUniformItemSizes(False)
        self.feed.setWordWrap(True)
        v.addWidget(self.feed)
        return box

    # ------------------------------------------------------------------ actions
    def _collect_settings(self) -> dict[str, Any]:
        return {
            **self.settings,
            "server": self.server_edit.text().strip(),
            "port": self.port_spin.value() or None,
            "slot_name": self.slot_edit.text().strip(),
            "adapter": self.adapter_combo.currentData(),
            "save_path": self.save_edit.text().strip() or None,
            "editor_dir": self.editor_edit.text().strip() or None,
            "sim_file": self.sim_edit.text().strip() or None,
        }

    def connect_clicked(self) -> None:
        from ..main import save_settings

        settings = self._collect_settings()
        if not settings["server"] or not settings["slot_name"]:
            QMessageBox.warning(self, "Missing details", "Enter the server address and your player name.")
            return
        try:
            adapter = self._build_adapter(settings["adapter"], self.catalogue, self.data_dir,
                                          sim_file=settings["sim_file"], save_path=settings["save_path"],
                                          editor_dir=settings["editor_dir"],
                                          steam_manifest=settings.get("steam_manifest"))
        except Exception as exc:
            QMessageBox.critical(self, "Game setup problem", str(exc))
            return
        self.settings = settings
        save_settings(self.data_dir, settings)  # the password is never written to disk
        if settings["adapter"] == "save" and getattr(adapter, "backend_error", None):
            self.add_feed(ev.Event(ev.ERROR, f"Save editor folder not usable: {adapter.backend_error}. "
                                             "Checks cannot be detected until this is fixed."))

        self.data_dir.mkdir(parents=True, exist_ok=True)
        ledger = Ledger(self.data_dir / "ledger.sqlite3")
        bus = ev.EventBus()
        bus.subscribe(self.bridge.event.emit)  # Qt queues this onto the UI thread
        config = ClientConfig(server=settings["server"], port=settings["port"], slot_name=settings["slot_name"],
                              password=self.password_edit.text(), data_dir=self.data_dir)
        controller = SyncController(config, adapter, ledger, self.catalogue, bus)
        self.worker = ControllerThread(controller, ledger)
        self.worker.start()
        self._set_running(True)

    def disconnect_clicked(self) -> None:
        if self.worker:
            worker, self.worker = self.worker, None
            worker.stop()
        self._set_running(False)

    def _set_running(self, running: bool) -> None:
        self.connect_btn.setEnabled(not running)
        self.disconnect_btn.setEnabled(running)
        for w in (self.server_edit, self.port_spin, self.slot_edit, self.password_edit, self.adapter_combo,
                  self.save_row, self.editor_row, self.sim_row):
            w.setEnabled(not running)

    def resolve_recovery(self, decision: str) -> None:
        if not (self.worker and self.recovery_batches):
            return
        batch = self.recovery_batches[0]
        text = ("Mark this reward as already delivered? Only choose this if you can see it in your inventory."
                if decision == "delivered" else
                "Deliver this reward again? Only choose this if it is NOT in your inventory, or you may get it twice.")
        if QMessageBox.question(self, "Interrupted delivery", text) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.worker.call(self.worker.controller.resolve_recovery(batch, decision))
        except Exception as exc:
            QMessageBox.critical(self, "Could not resolve", str(exc))
            return
        self._refresh_recovery()

    def _refresh_recovery(self) -> None:
        worker = self.worker
        if not worker or not worker.controller.session:
            self.recovery_batches = []
        else:
            sid = worker.controller.session.id
            self.recovery_batches = [b.batch_id for b in worker.ledger.batches(sid, (BatchState.RECOVERY,))]
        if self.recovery_batches:
            self.recovery_label.setText(
                f"<b>A reward delivery was interrupted</b> ({len(self.recovery_batches)} waiting). "
                "Quit the game, check your inventory, then tell Crimsonipelago what you see.")
            self.recovery.show()
        else:
            self.recovery.hide()

    # ------------------------------------------------------------------ events
    def _on_event(self, event: ev.Event) -> None:
        if event.kind == ev.STATUS:
            self._apply_status(event.data["status"])
            return
        self.add_feed(event)
        if event.kind == ev.RECOVERY_REQUIRED:
            self._refresh_recovery()
        if self.worker and self.worker.finished.is_set():
            self.worker = None
            self._set_running(False)

    def add_feed(self, event: ev.Event) -> None:
        tag, colour = FEED_STYLE.get(event.kind, ("INFO", "#757575"))
        item = QListWidgetItem(f"{event.at:%H:%M:%S}  {tag:<6}  {event.message}")
        item.setForeground(QColor(colour))
        at_bottom = self.feed.verticalScrollBar().value() >= self.feed.verticalScrollBar().maximum() - 4
        self.feed.addItem(item)
        if self.feed.count() > 2000:
            self.feed.takeItem(0)
        if at_bottom:
            self.feed.scrollToBottom()

    def _apply_status(self, s: StatusSnapshot) -> None:
        text, colour = CONNECTION_LABELS.get(s.connection, (s.connection.title(), "#9e9e9e"))
        self.conn_pill.setText(text)
        self.conn_pill.setStyleSheet(f"background: {colour}; color: white; border-radius: 10px; padding: 4px 12px;"
                                     " font-weight: 600;")
        self.conn_pill.setToolTip(s.connection_detail or "")

        f = self.fields
        f["seed"].setText(s.seed_name or "—")
        game = s.game_version or "not detected yet"
        if s.game_version:
            game += " · supported" if s.game_supported else " · untested version"
            game += "" if s.writes_allowed else " · read-only"
        f["game"].setText(game)
        f["profile"].setText(s.profile_id or "no save loaded")
        f["checks"].setText(f"{s.checks_confirmed} confirmed / {s.checks_detected} found")
        f["rewards"].setText(str(s.delivered))
        f["pending"].setText(str(s.pending_deliveries) + (f" · {s.waiting_reason}" if s.waiting_reason else ""))
        f["catalogue"].setText(s.catalogue_status + (" (development data)" if s.catalogue_status == "placeholder"
                                                     else ""))
        f["held"].setText(f"{s.unsupported_items} not deliverable yet" if s.unsupported_items else "none")
        f["goal"].setText("reported ✔" if s.goal_reported else ("reached, sending…" if s.goal_detected
                                                                 else "not reached"))

        if s.paused:
            self._show_banner(f"<b>Synchronization paused.</b> {s.paused}", "#fff3e0", "#e65100")
        elif s.connection == "disconnected" and s.seed_name:
            self._show_banner("<b>Offline.</b> Checks you complete are kept and sent when the server is back. "
                              + (s.connection_detail or ""), "#eceff1", "#37474f")
        else:
            self.banner.hide()
        if (s.recovery_required > 0) != bool(self.recovery_batches):
            self._refresh_recovery()

    def _show_banner(self, html: str, bg: str, fg: str) -> None:
        self.banner.setStyleSheet(f"QFrame#banner {{ background: {bg}; color: {fg}; }}")
        self.banner_label.setText(html)
        self.banner.show()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self.disconnect_clicked()
        super().closeEvent(event)


def run_ui(data_dir: Path, catalogue: Catalogue, *, adapter_kind: str = "save",
           adapter_options: dict[str, Any] | None = None) -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Crimsonipelago")
    app.setWindowIcon(app_icon())
    window = MainWindow(data_dir, catalogue, adapter_kind, adapter_options)
    window.show()
    return app.exec()

