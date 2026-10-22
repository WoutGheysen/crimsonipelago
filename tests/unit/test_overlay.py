"""Game-data overlays: same-size record patches, install/uninstall as an extra archive group."""

import json
import struct
from pathlib import Path

import pytest

from crimsonipelago.game_data import overlay as ov
from crimsonipelago.game_data.archive import GameArchive, Papgt, build_group
from crimsonipelago.game_data.checksum import checksum
from crimsonipelago.game_data.tables import Table
from crimsonipelago.tools import mod_cli

DIR = "gamedata/binary__/client/bin"


def _record(key, name, payload=b""):
    n = name.encode()
    return struct.pack("<II", key, len(n)) + n + payload


def _dropset_table():
    recs = [(1014511, _record(1014511, "Knowledge(Knowledge_Other, 1)", struct.pack("<I", 777))),
            (1014512, _record(1014512, "Knowledge(Knowledge_CrowWing, 1)",
                              struct.pack("<II", 40002, 0) + struct.pack("<I", 40002) + b"Knowledge(Knowledge_CrowWing, 1)"))]
    body, index = b"", []
    for key, data in recs:
        index.append(struct.pack("<II", key, len(body)))
        body += data
    return body, struct.pack("<H", len(index)) + b"".join(index)


def _game(tmp_path: Path) -> Path:
    root = tmp_path / "Crimson Desert"
    body, header = _dropset_table()
    papgt = Papgt(1360, 0, 2, 0, [], b"")
    for name, files in reversed([("0000", {f"{DIR}/dropsetinfo.staticinfobody": body,
                                           f"{DIR}/dropsetinfo.staticinfoheader": header}),
                                 ("0001", {"ui/readme.txt": b"hi"})]):
        pamt, chunks = build_group(files)
        (root / name).mkdir(parents=True)
        (root / name / "0.pamt").write_bytes(pamt)
        for cid, data in chunks.items():
            (root / name / f"{cid}.paz").write_bytes(data)
        papgt.add_front(name, checksum(pamt[12:]))
    (root / "meta").mkdir()
    (root / "meta" / "0.papgt").write_bytes(papgt.to_bytes())
    return root


@pytest.fixture
def no_process_check(monkeypatch):
    import crimsonipelago.game_adapter.process_check as pc
    monkeypatch.setattr(pc, "game_running", lambda *a, **k: False)


def test_bundled_flight_patch_applies_in_place():
    body, header = _dropset_table()
    table = Table("dropsetinfo", body, header)
    new, log = ov.apply_patches(table, ov.PatchSpec.load("flight-grant-removed").patches)
    assert len(new) == len(body)
    patched = Table("dropsetinfo", new, header)
    assert patched.records[1014512].name == "Knowledge(Knowledge_Hedgehog, 1)"
    assert struct.pack("<I", 40002) not in patched.records[1014512].data
    assert patched.records[1014511].data == table.records[1014511].data
    assert "2 value(s), 2 text(s)" in log[0]


def test_patch_guards(tmp_path):
    body, header = _dropset_table()
    table = Table("dropsetinfo", body, header)
    wrong_name = ov.RecordPatch("dropsetinfo", 1014512, name="Something else", u32=[(40002, 1006)])
    with pytest.raises(ov.OverlayError, match="expected record"):
        ov.apply_patches(table, [wrong_name])
    wrong_count = ov.RecordPatch("dropsetinfo", 1014512, u32=[(40002, 1006)], expect_u32=3)
    with pytest.raises(ov.OverlayError, match="expected 3"):
        ov.apply_patches(table, [wrong_count])
    with pytest.raises(ov.OverlayError, match="not found"):
        ov.apply_patches(table, [ov.RecordPatch("dropsetinfo", 1, u32=[(1, 2)])])
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"name": "x", "patches": [{"table": "t", "record": 1, "text": [["ab", "abc"]]}]}))
    with pytest.raises(ov.OverlayError, match="changes the length"):
        ov.PatchSpec.load(bad)


def test_install_and_uninstall_are_exact_inverses(tmp_path, no_process_check):
    root = _game(tmp_path)
    original = (root / "meta" / "0.papgt").read_bytes()
    built = ov.build(GameArchive(root), ov.PatchSpec.load("flight-grant-removed"))
    result = ov.install(root, built)
    assert result.name == "0090"
    game = GameArchive(root)
    assert game.groups[0].name == "0090"
    [vf] = game.files("dropsetinfo.staticinfobody")
    assert vf.group == "0090"
    assert Table("dropsetinfo", game.read(vf), game.read(game.files("dropsetinfo.staticinfoheader")[0])) \
        .records[1014512].name == "Knowledge(Knowledge_Hedgehog, 1)"
    with pytest.raises(ov.OverlayError, match="already installed"):
        ov.install(root, built)
    # building again reads the vanilla table, not our own overlay
    assert ov.build(GameArchive(root), ov.PatchSpec.load("flight-grant-removed")).sources[vf.path] == "0000"

    assert ov.uninstall(root) == ["0090"]
    assert (root / "meta" / "0.papgt").read_bytes() == original
    assert not (root / "0090").exists()
    assert len(list((root / "meta").glob("0.papgt.crimsonipelago-*.bak"))) >= 1


def test_failed_install_leaves_nothing_behind(tmp_path, no_process_check, monkeypatch):
    root = _game(tmp_path)
    original = (root / "meta" / "0.papgt").read_bytes()
    built = ov.build(GameArchive(root), ov.PatchSpec.load("flight-grant-removed"))
    monkeypatch.setattr(ov, "_verify_installed", lambda *a: (_ for _ in ()).throw(ov.OverlayError("boom")))
    with pytest.raises(ov.OverlayError, match="boom"):
        ov.install(root, built)
    assert (root / "meta" / "0.papgt").read_bytes() == original
    assert not (root / "0090").exists()


def test_refuses_while_game_runs(tmp_path, monkeypatch):
    import crimsonipelago.game_adapter.process_check as pc
    root = _game(tmp_path)
    built = ov.build(GameArchive(root), ov.PatchSpec.load("flight-grant-removed"))
    monkeypatch.setattr(pc, "game_running", lambda *a, **k: True)
    with pytest.raises(ov.OverlayError, match="running"):
        ov.install(root, built)
    monkeypatch.setattr(pc, "game_running", lambda *a, **k: None)
    with pytest.raises(ov.OverlayError, match="cannot tell"):
        ov.install(root, built)


def test_foreign_groups_are_never_deleted(tmp_path, no_process_check):
    root = _game(tmp_path)
    (root / "0095").mkdir()
    (root / "0095" / "0.pamt").write_bytes(b"someone else's mod")
    ov.install(root, ov.build(GameArchive(root), ov.PatchSpec.load("flight-grant-removed")))
    ov.uninstall(root)
    assert (root / "0095" / "0.pamt").read_bytes() == b"someone else's mod"


def test_cli(tmp_path, capsys, no_process_check):
    root = _game(tmp_path)
    assert mod_cli.main(["list"]) == 0
    assert mod_cli.main(["--game", str(root), "check", "flight-grant-removed"]) == 0
    assert mod_cli.main(["--game", str(root), "install", "flight-grant-removed"]) == 0
    assert mod_cli.main(["--game", str(root), "status"]) == 0
    assert "active (loads first)" in capsys.readouterr().out
    assert mod_cli.main(["--game", str(root), "uninstall"]) == 0
