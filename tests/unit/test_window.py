"""The desktop window's own logic, rendered offscreen (no server)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from crimsonipelago.client.synchronization import StatusSnapshot  # noqa: E402


@pytest.fixture
def window(tmp_path, real_catalogue, monkeypatch):
    from crimsonipelago.client.ui.main_window import MainWindow

    monkeypatch.setenv("CRIMSONIPELAGO_BUNDLED", str(tmp_path / "no-bundle"))
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = MainWindow(tmp_path / "data", real_catalogue, "save", {}, settings={})
    w.show()
    app.processEvents()
    yield w
    w.close()


def test_checks_list_groups_by_chapter_in_story_order(window, real_catalogue):
    ids = [loc.ap_id for loc in real_catalogue.locations_in_scope("chapter_2") if loc.category == "quest"]
    window.show_checks({"enabled": ids, "states": {ids[0]: "confirmed", ids[1]: "sent", ids[2]: "found"}})
    tree = window.checks_tree
    assert tree.topLevelItemCount() == 2
    first = tree.topLevelItem(0)
    assert first.text(0).startswith("Prologue and chapter 1: 2/") and first.isExpanded()
    assert first.child(0).text(0).startswith("✔") and first.child(2).text(0).startswith("•")
    assert tree.topLevelItem(1).text(0).startswith("Chapter 2: 0/")
    assert "(draft)" in tree.topLevelItem(1).child(0).text(0)
    assert window.checks_summary.text().startswith(f"2 of {len(ids)} checks done")


def test_override_problem_shows_a_fix_button(window):
    window._apply_status(StatusSnapshot(override_problem="The story still hands out Back Hang.",
                                        override_action="Switch to story-abilities-removed"))
    assert window.override.isVisible() and window.override_btn.text() == "Switch to story-abilities-removed"
    window._apply_status(StatusSnapshot())
    assert not window.override.isVisible()


def test_without_a_bundled_plugin_the_install_button_stays_hidden(window, tmp_path):
    game = tmp_path / "game"
    (game / "bin64").mkdir(parents=True)
    (game / "meta").mkdir()
    (game / "meta" / "0.papgt").write_bytes(b"")
    window.game_edit.setText(str(game))
    window.refresh_plugin()
    assert not window.plugin_btn.isVisible() and "Not installed" in window.plugin_label.text()
