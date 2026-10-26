"""The window's buttons, without the window."""

import json
import zipfile

import pytest

from crimsonipelago.client import actions


def test_log_bundle_has_logs_and_settings_but_never_the_password(tmp_path):
    data = tmp_path / "data"
    (data / "logs").mkdir(parents=True)
    (data / "logs" / "live-20261026.jsonl").write_text('{"event": "give"}\n')
    (data / "logs" / "other.txt").write_text("not a play log")
    (data / "settings.json").write_text(json.dumps({"server": "archipelago.gg:1234", "password": "hunter2"}))
    (data / "found-targets.json").write_text("{}")
    game = tmp_path / "game"
    (game / "bin64").mkdir(parents=True)
    (game / "bin64" / actions.PLUGIN_LOG).write_text("plugin started")
    out = actions.bundle_logs(data, game_dir=game, summary="seed 123")
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        assert names == {"logs/live-20261026.jsonl", "settings.json", "found-targets.json", actions.PLUGIN_LOG,
                         "about.txt"}
        assert "hunter2" not in z.read("settings.json").decode() and "archipelago.gg" in z.read("settings.json").decode()
        assert "seed 123" in z.read("about.txt").decode()
    assert out.parent == data / "support"


@pytest.fixture
def bundled(tmp_path, monkeypatch):
    root = tmp_path / "bundled"
    (root / "plugin").mkdir(parents=True)
    (root / "plugin" / actions.PLUGIN_FILE).write_bytes(b"plugin v2")
    (root / "plugin" / actions.LOADER_FILE).write_bytes(b"MZ... Ultimate ASI Loader ...")
    (root / "CrimsonSaveEditor").mkdir()
    (root / "CrimsonSaveEditor" / "save_parser.py").write_text("")
    monkeypatch.setenv("CRIMSONIPELAGO_BUNDLED", str(root))
    return root


def _game(tmp_path):
    game = tmp_path / "game"
    (game / "bin64").mkdir(parents=True)
    return game


def test_the_release_carries_the_save_editor_decoder(bundled):
    assert actions.default_editor_dir() == bundled / "CrimsonSaveEditor"


def test_plugin_install_copies_plugin_and_loader(tmp_path, bundled):
    game = _game(tmp_path)
    state = actions.plugin_state(game)
    assert (state.plugin, state.loader, state.bundled, state.can_install) == ("missing", "missing", True, True)
    done = actions.install_plugin(state, game_running=lambda: False)
    assert len(done) == 2 and (game / "bin64" / actions.LOADER_FILE).read_bytes().startswith(b"MZ")
    assert actions.plugin_state(game).ready


def test_plugin_install_updates_an_old_plugin_and_never_replaces_another_version_dll(tmp_path, bundled):
    game = _game(tmp_path)
    (game / "bin64" / actions.PLUGIN_FILE).write_bytes(b"plugin v1")
    (game / "bin64" / actions.LOADER_FILE).write_bytes(b"some other library")
    state = actions.plugin_state(game)
    assert (state.plugin, state.loader) == ("outdated", "other") and state.notes
    assert actions.install_plugin(state, game_running=lambda: False) == [f"updated {actions.PLUGIN_FILE}"]
    assert (game / "bin64" / actions.LOADER_FILE).read_bytes() == b"some other library"
    assert actions.plugin_state(game).ready


def test_plugin_install_waits_for_the_game_to_close(tmp_path, bundled):
    state = actions.plugin_state(_game(tmp_path))
    with pytest.raises(RuntimeError, match="close Crimson Desert"):
        actions.install_plugin(state, game_running=lambda: True)


def test_a_build_without_the_plugin_files_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("CRIMSONIPELAGO_BUNDLED", str(tmp_path / "nothing"))
    monkeypatch.setattr(actions, "bundled_root", lambda: None)
    state = actions.plugin_state(_game(tmp_path))
    assert not state.bundled and not state.can_install
    with pytest.raises(RuntimeError, match="doesn't carry"):
        actions.install_plugin(state, game_running=lambda: False)
