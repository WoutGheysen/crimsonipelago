"""Crimsonipelago desktop client (scope doc section 8), built with PySide6.

The synchronization controller runs on its own asyncio loop in a background
thread. Everything it reports reaches the window through the EventBus, which
is bridged into Qt with a queued signal, so the UI thread never blocks on the
network, the save file or the ledger.
"""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path
from typing import Any

from crimson_desert.catalogue import Catalogue
from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPixmap
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
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ... import __version__
from .. import actions
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

PLUGIN_LABELS = {  # StatusSnapshot.plugin -> (text, colour); anything else is a problem, shown as is
    "off": ("Live delivery off", "#9e9e9e"),
    "waiting": ("Game not running", "#78909c"),
    "connected": ("Game plugin connected", "#2e7d32"),
}

CHECK_MARKS = {"confirmed": ("✔", "#2e7d32"), "sent": ("↑", "#558b2f"), "found": ("•", "#ef6c00")}


def chapter_label(slice_no: int) -> str:
    if slice_no <= 1:
        return "Prologue and chapter 1"
    if slice_no >= 13:
        return "Epilogue and the whole map"
    return f"Chapter {slice_no}"


def find_game_dir(chosen: str | None) -> Path | None:
    from ...tools.game_data_cli import find_game_dir as find

    try:
        return find(chosen or None)
    except SystemExit:
        return None


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

    def __init__(self, controller: SyncController, ledger: Ledger, live=None):
        self.controller, self.ledger, self.live = controller, ledger, live
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
            if self.live is not None:
                self.live.close()
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
            "game_dir": opts.get("game_dir") or self.settings.get("game_dir"),
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
        layout.addWidget(self._override_bar())
        layout.addWidget(self._doubt_bar())
        layout.addWidget(self._recovery_bar())
        layout.addWidget(self._status_box())
        self.tabs = QTabWidget()
        self.tabs.addTab(self._feed_box(), "Activity")
        self.tabs.addTab(self._checks_box(), "Checks")
        self.tabs.currentChanged.connect(lambda i: i == 1 and self.refresh_checks())
        layout.addWidget(self.tabs, 1)
        layout.addLayout(self._tools_row())

        self._apply_status(StatusSnapshot(server=self.server_edit.text(), slot_name=self.slot_edit.text(),
                                          catalogue_status=catalogue.status))
        self._sync_adapter_fields()
        self.refresh_plugin()
        self._checks_timer = QTimer(self, singleShot=True, interval=1500, timeout=self.refresh_checks)

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
        self.plugin_pill = QLabel()
        self.plugin_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.plugin_pill.setMinimumWidth(170)
        h.addWidget(title)
        h.addSpacing(8)
        h.addWidget(sub)
        h.addStretch(1)
        h.addWidget(self.plugin_pill)
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
        self.adapter_combo.addItem("Your game", "save")
        self.adapter_combo.addItem("Simulated game (testing)", "sim")
        self.adapter_combo.setCurrentIndex(max(0, self.adapter_combo.findData(self.adapter_kind)))
        self.adapter_combo.currentIndexChanged.connect(self._sync_adapter_fields)
        self.game_edit = QLineEdit(self.adapter_options.get("game_dir") or "")
        found = find_game_dir(None)
        self.game_edit.setPlaceholderText(f"found: {found}" if found else "the Crimson Desert folder (with bin64)")
        self.game_edit.editingFinished.connect(self.refresh_plugin)
        self.save_edit = QLineEdit(self.adapter_options.get("save_path") or "")
        self.save_edit.setPlaceholderText("automatic: your newest save")
        self.editor_edit = QLineEdit(self.adapter_options.get("editor_dir") or "")
        bundled = actions.default_editor_dir()
        self.editor_edit.setPlaceholderText("built in" if bundled else "CrimsonSaveEditor folder (needed to read checks)")
        self.sim_edit = QLineEdit(self.adapter_options.get("sim_file") or "")
        self.sim_edit.setPlaceholderText(str(self.data_dir / "simulated_game.json"))
        self.game_row = self._path_row(self.game_edit, True, "Choose the Crimson Desert folder")
        self.save_row = self._path_row(self.save_edit, False, "Choose save.save", "Crimson Desert save (*.save)")
        self.editor_row = self._path_row(self.editor_edit, True, "Choose your CrimsonSaveEditor folder")
        self.sim_row = self._path_row(self.sim_edit, False, "Choose simulated game file", "JSON (*.json)")
        plugin = QWidget()
        ph = QHBoxLayout(plugin)
        ph.setContentsMargins(0, 0, 0, 0)
        self.plugin_label = QLabel()
        self.plugin_label.setWordWrap(True)
        self.plugin_btn = QPushButton("Install plugin")
        self.plugin_btn.clicked.connect(self.install_plugin_clicked)
        ph.addWidget(self.plugin_label, 1)
        ph.addWidget(self.plugin_btn)
        self.plugin_row = plugin
        form.addRow("Game folder", self.game_row)
        form.addRow("Game plugin", self.plugin_row)
        form.addRow("Save file", self.save_row)
        form.addRow("Save editor", self.editor_row)
        form.addRow("Game source", self.adapter_combo)
        form.addRow("Sim file", self.sim_row)
        self.game_form = form
        return box

    def _sync_adapter_fields(self) -> None:
        sim = self.adapter_combo.currentData() == "sim"
        for row, visible in ((self.save_row, not sim), (self.editor_row, not sim), (self.sim_row, sim),
                             (self.game_row, not sim), (self.plugin_row, not sim)):
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

    def _override_bar(self) -> QFrame:
        self.override = QFrame(objectName="banner")
        self.override.setStyleSheet("QFrame#banner { background: #fff3e0; color: #e65100; }")
        h = QHBoxLayout(self.override)
        h.setContentsMargins(10, 6, 10, 6)
        self.override_label = QLabel()
        self.override_label.setWordWrap(True)
        self.override_btn = QPushButton("Fix it", objectName="primary")
        self.override_btn.clicked.connect(self.override_clicked)
        h.addWidget(self.override_label, 1)
        h.addWidget(self.override_btn)
        self.override.hide()
        return self.override

    def _doubt_bar(self) -> QFrame:
        self.doubt = QFrame(objectName="banner")
        self.doubt.setStyleSheet("QFrame#banner { background: #ffebee; color: #b71c1c; }")
        h = QHBoxLayout(self.doubt)
        h.setContentsMargins(10, 6, 10, 6)
        self.doubt_label = QLabel()
        self.doubt_label.setWordWrap(True)
        self.doubt_btn = QPushButton()
        self.doubt_btn.clicked.connect(self.doubt_clicked)
        h.addWidget(self.doubt_label, 1)
        h.addWidget(self.doubt_btn)
        self.doubt.hide()
        self._doubt_kind: str | None = None
        return self.doubt

    def doubt_clicked(self) -> None:
        worker = self.worker
        if worker is None or self._doubt_kind is None:
            return
        if self._doubt_kind == "goal":
            text = ("Report the goal to the server?\n\nThis can't be undone. If the room releases on goal, everything "
                    "left in your world goes out to everyone. Only do this if you really finished.")
            call = worker.controller.confirm_goal
        else:
            text = ("Send all these checks?\n\nChecks can't be taken back. Only do this if you really did all of them "
                    "(for example, you played a long stretch with the client closed).")
            call = worker.controller.confirm_checks
        if QMessageBox.question(self, "Are you sure?", text) == QMessageBox.StandardButton.Yes:
            try:
                worker.call(call())
            except Exception as exc:
                QMessageBox.warning(self, "Couldn't send", str(exc))

    def _checks_box(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 6, 0, 0)
        self.checks_summary = QLabel("Connect to a seed to see its checks.")
        self.checks_tree = QTreeWidget()
        self.checks_tree.setHeaderHidden(True)
        self.checks_tree.setUniformRowHeights(True)
        v.addWidget(self.checks_summary)
        v.addWidget(self.checks_tree, 1)
        return w

    def _tools_row(self) -> QHBoxLayout:
        h = QHBoxLayout()
        self.rebind_btn = QPushButton("Move seed to my newest save…")
        self.rebind_btn.setToolTip("When you saved over the playthrough this seed was on, or started a new one")
        self.rebind_btn.clicked.connect(self.rebind_clicked)
        self.logs_btn = QPushButton("Send logs…")
        self.logs_btn.setToolTip("Zip today's play logs for a bug report (no password, no saves)")
        self.logs_btn.clicked.connect(self.logs_clicked)
        folder = QPushButton("Open data folder")
        folder.clicked.connect(lambda: self._open_folder(self.data_dir))
        h.addWidget(self.rebind_btn)
        h.addStretch(1)
        h.addWidget(folder)
        h.addWidget(self.logs_btn)
        return h

    def _status_box(self) -> QGroupBox:
        box = QGroupBox("Status")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        self.fields: dict[str, QLabel] = {}
        rows = [
            [("seed", "Seed"), ("game", "Game version"), ("profile", "Playthrough")],
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
            "game_dir": self.game_edit.text().strip() or None,
        }

    def _game_dir(self) -> Path | None:
        return find_game_dir(self.game_edit.text().strip() or None)

    def _editor_dir(self, settings: dict[str, Any]) -> str | None:
        chosen = settings.get("editor_dir")
        bundled = actions.default_editor_dir()
        return chosen or (str(bundled) if bundled else None)

    def connect_clicked(self) -> None:
        from ..main import save_settings

        settings = self._collect_settings()
        if not settings["server"] or not settings["slot_name"]:
            QMessageBox.warning(self, "Missing details", "Enter the server address and your player name.")
            return
        if settings.get("game_dir"):
            os.environ["CRIMSON_DESERT_DIR"] = settings["game_dir"]   # the override and plugin checks look here
        try:
            adapter = self._build_adapter(settings["adapter"], self.catalogue, self.data_dir,
                                          sim_file=settings["sim_file"], save_path=settings["save_path"],
                                          editor_dir=self._editor_dir(settings),
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
        live = None
        if settings["adapter"] == "save":
            from ...game_adapter.runtime.live import JsonlLog, LiveChannel

            # items arrive while you play; every delivery goes in logs/live-YYYYMMDD.jsonl
            live = LiveChannel(on_event=lambda message: bus.emit(ev.CONNECTION, message),
                               log=JsonlLog(self.data_dir / "logs"), found_file=self.data_dir / "found-targets.json")
        controller = SyncController(config, adapter, ledger, self.catalogue, bus, live=live)
        self.worker = ControllerThread(controller, ledger, live)
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
                  self.save_row, self.editor_row, self.sim_row, self.game_row, self.rebind_btn):
            w.setEnabled(not running)
        self.refresh_plugin()

    # the game plugin ---------------------------------------------------------
    def refresh_plugin(self) -> None:
        state = actions.plugin_state(self._game_dir())
        self.plugin_state = state
        if state.game_dir is None:
            text = "Game folder not found: choose it above."
        elif state.ready:
            text = "Installed. Items arrive while you play."
        elif state.plugin == "outdated":
            text = "An older version is installed."
        else:
            text = "Not installed: items only arrive when you quit to desktop."
        if not state.bundled and not state.ready:
            text += " (This build doesn't carry the plugin: see docs/plugin.md.)"
        self.plugin_label.setText(text)
        self.plugin_label.setToolTip("\n".join(state.notes))
        self.plugin_btn.setText("Update plugin" if state.plugin == "outdated" else "Install plugin")
        self.plugin_btn.setVisible(state.can_install)
        self.plugin_btn.setEnabled(state.can_install and self.worker is None)

    def install_plugin_clicked(self) -> None:
        from ...game_adapter.process_check import game_running

        try:
            done = actions.install_plugin(self.plugin_state, game_running=lambda: bool(game_running()))
        except Exception as exc:
            QMessageBox.warning(self, "Game plugin", str(exc))
            return
        self.add_feed(ev.Event(ev.CONNECTION, "Game plugin: " + "; ".join(done)))
        self.refresh_plugin()

    # the game-data override ----------------------------------------------------
    def override_clicked(self) -> None:
        worker = self.worker
        advice = getattr(worker.controller, "override_advice", None) if worker else None
        if advice is None:
            return
        what = (f"install the game-data override {advice.best!r}" if advice.best else "remove the game-data override")
        if QMessageBox.question(self, "Game-data override",
                                f"Close Crimson Desert first. Then {what}?\n\n{advice.message()}") \
                != QMessageBox.StandardButton.Yes:
            return
        from ...game_adapter.overrides import switch

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            done = switch(advice)
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Game-data override", str(exc))
            return
        QApplication.restoreOverrideCursor()
        self.add_feed(ev.Event(ev.CONNECTION, "Game-data override: " + "; ".join(done)))
        try:
            worker.call(worker.controller.recheck_override())
        except Exception as exc:
            self.add_feed(ev.Event(ev.ERROR, f"Couldn't check the override again: {exc}"))

    # moving the seed to another playthrough -------------------------------------
    def rebind_clicked(self) -> None:
        if self.worker is not None:
            return
        settings = self._collect_settings()
        try:
            adapter = self._build_adapter(settings["adapter"], self.catalogue, self.data_dir,
                                          sim_file=settings["sim_file"], save_path=settings["save_path"],
                                          editor_dir=self._editor_dir(settings),
                                          steam_manifest=settings.get("steam_manifest"))
        except Exception as exc:
            QMessageBox.critical(self, "Game setup problem", str(exc))
            return
        ledger = Ledger(self.data_dir / "ledger.sqlite3")
        try:
            plan = actions.rebind_plan(ledger, adapter, self.catalogue)
            title = "Move seed to my newest save"
            if plan.problem:
                QMessageBox.information(self, title, plan.problem)
                return
            if plan.nothing_to_do:
                QMessageBox.information(self, title, f"Seed {plan.seed} is already on the playthrough of your "
                                                     "newest save. Nothing to do.")
                return
            if QMessageBox.question(
                    self, title,
                    f"Seed {plan.seed} is on playthrough {plan.from_profile}. Your newest save is from playthrough "
                    f"{plan.to_profile}.\n\nMove the seed there? Checks already sent stay sent.") \
                    != QMessageBox.StandardButton.Yes:
                return
            give_again = False
            if plan.to_give:
                give_again = QMessageBox.question(
                    self, title,
                    f"The seed already gave you {len(plan.to_give)} item(s).\n\nGive them again? Yes if this "
                    "playthrough doesn't have them (a new or different playthrough). No if it's the one they went "
                    "into (you're moving back).") == QMessageBox.StandardButton.Yes
            given = actions.apply_rebind(ledger, plan, give_again=give_again)
            QMessageBox.information(self, title, f"Done: the seed continues on this playthrough. {given} item(s) "
                                                 "will be given again. Press Connect.")
        finally:
            ledger.close()

    # logs ----------------------------------------------------------------------
    def logs_clicked(self) -> None:
        f = self.fields
        summary = "\n".join(f"{k}: {v.text()}" for k, v in f.items())
        try:
            path = actions.bundle_logs(self.data_dir, game_dir=self._game_dir(), summary=summary)
        except OSError as exc:
            QMessageBox.warning(self, "Send logs", f"Couldn't make the zip: {exc}")
            return
        box = QMessageBox(self)
        box.setWindowTitle("Send logs")
        box.setText(f"Made {path.name}.\n\nAttach it to your message or GitHub issue. It has the play logs and "
                    "the client's settings, never your password or your saves.")
        open_btn = box.addButton("Open folder", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is open_btn:
            self._open_folder(path.parent)

    def _open_folder(self, folder: Path) -> None:
        Path(folder).mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # the checks list -------------------------------------------------------------
    def refresh_checks(self) -> None:
        worker = self.worker
        if worker is None or worker.finished.is_set():
            return
        try:
            overview = worker.call(worker.controller.check_overview(), timeout=5)
        except Exception:
            return
        self.show_checks(overview)

    def show_checks(self, overview: dict) -> None:
        self.checks_tree.clear()
        enabled, states = overview.get("enabled", []), overview.get("states", {})
        if not enabled:
            self.checks_summary.setText("Connect to a seed to see its checks.")
            return
        by_chapter: dict[int, list] = {}
        order = {loc.ap_id: i for i, loc in enumerate(self.catalogue.locations.values())}   # story order
        for loc_id in enabled:
            loc = self.catalogue.location_by_id(loc_id)
            if loc is None:
                continue
            by_chapter.setdefault(self.catalogue.region_slices.get(loc.region, 1), []).append(loc)
        done = sum(1 for loc_id in enabled if states.get(loc_id) in ("confirmed", "sent"))
        self.checks_summary.setText(f"{done} of {len(enabled)} checks done. ✔ sent and confirmed · ↑ sent · "
                                    "• done in your save, sending soon")
        expanded = False
        for chapter in sorted(by_chapter):
            locs = by_chapter[chapter]
            n = sum(1 for loc in locs if states.get(loc.ap_id) in ("confirmed", "sent"))
            top = QTreeWidgetItem([f"{chapter_label(chapter)}: {n}/{len(locs)}"])
            for loc in sorted(locs, key=lambda x: order.get(x.ap_id, x.ap_id)):
                mark, colour = CHECK_MARKS.get(states.get(loc.ap_id, ""), ("  ", "#757575"))
                child = QTreeWidgetItem([f"{mark}  {loc.name}" + ("" if loc.verified else "  (draft)")])
                child.setForeground(0, QColor(colour))
                top.addChild(child)
            self.checks_tree.addTopLevelItem(top)
            if n < len(locs) and not expanded:          # the chapter you're in
                top.setExpanded(True)
                expanded = True

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
        if event.kind in (ev.CHECK_SENT, ev.CHECK_DETECTED, ev.CONNECTION) and self.tabs.currentIndex() == 1:
            self._checks_timer.start()
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
        ptext, pcolour = PLUGIN_LABELS.get(s.plugin, ("Game plugin: problem", "#ef6c00"))
        self.plugin_pill.setText(ptext)
        self.plugin_pill.setToolTip("" if s.plugin in PLUGIN_LABELS else s.plugin)
        self.plugin_pill.setStyleSheet(f"background: {pcolour}; color: white; border-radius: 10px; "
                                       "padding: 4px 12px; font-weight: 600;")
        self.plugin_pill.setVisible(self.worker is not None and s.plugin != "off")
        if s.goal_doubt or s.checks_doubt:
            self._doubt_kind = "goal" if s.goal_doubt else "checks"
            what = (f"<b>Goal not reported.</b> {s.goal_doubt[0].upper() + s.goal_doubt[1:]}." if s.goal_doubt else
                    f"<b>Checks held back.</b> {s.checks_doubt}, which usually means the save was read wrong (a game "
                    "update?).")
            self.doubt_label.setText(what + " Send logs if this looks wrong.")
            self.doubt_btn.setText("Report goal anyway…" if s.goal_doubt else "Send them…")
            self.doubt.show()
        else:
            self.doubt.hide()
        if s.override_problem:
            self.override_label.setText(f"<b>Game-data override.</b> {s.override_problem}")
            self.override_btn.setText(s.override_action or "Fix it")
            self.override_btn.setVisible(bool(s.override_action))
            self.override.show()
        else:
            self.override.hide()

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

