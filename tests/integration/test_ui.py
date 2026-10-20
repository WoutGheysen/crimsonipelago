"""Desktop UI against a real Archipelago server, rendered offscreen.

Set CRIMSONIPELAGO_SCREENSHOT=/path/to/file.png to keep a screenshot of the window.
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from crimsonipelago.game_adapter.sim_adapter import SimulatedGameAdapter  # noqa: E402
from tests.integration.conftest import AP  # noqa: E402

pytestmark = [pytest.mark.archipelago, pytest.mark.skipif(AP is None, reason="ARCHIPELAGO_PATH not set")]


def pump_until(app, predicate, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("UI condition not met in time")


def test_window_connects_plays_and_disconnects(multiworld, tmp_path, catalogue):
    from crimsonipelago.client.ui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    sim_file = tmp_path / "sim.json"
    sim = SimulatedGameAdapter(sim_file)
    window = MainWindow(tmp_path / "data", catalogue, "sim", {"sim_file": str(sim_file)}, settings={})
    window.show()
    window.server_edit.setText(multiworld["url"])
    window.slot_edit.setText("Wout")
    window.connect_clicked()
    try:
        pump_until(app, lambda: window.conn_pill.text() == "Connected")
        assert window.fields["seed"].text() != "—"
        assert window.fields["profile"].text() == "sim/slot1"
        assert not window.server_edit.isEnabled()

        for name in ("Prologue Quest 1", "Prologue Quest 2", "Prologue Discovery"):
            sim.complete_location(name)
        pump_until(app, lambda: window.fields["checks"].text().startswith("3 confirmed"))

        def feed():
            return [window.feed.item(i).text() for i in range(window.feed.count())]
        # in this seed the three prologue checks hold items for the ChecksFinder player
        pump_until(app, lambda: sum("SENT" in line for line in feed()) >= 1)
        shot = os.environ.get("CRIMSONIPELAGO_SCREENSHOT")
        if shot:
            window.grab().save(shot)
        assert any("CHECK" in line and "Prologue Quest 1" in line for line in feed())
    finally:
        window.disconnect_clicked()
    assert window.connect_btn.isEnabled() and window.server_edit.isEnabled()
    # nothing in settings.json may contain the password, and the address is remembered
    settings = (tmp_path / "data" / "settings.json").read_text(encoding="utf-8")
    assert "password" not in settings and "Wout" in settings
    window.close()
