"""crimsonipelago.game_data: checksum, archive reading/building, static-info tables, crimson-data CLI."""

import struct
from pathlib import Path

import pytest

from crimsonipelago.game_data import archive as ar
from crimsonipelago.game_data import tables as tb
from crimsonipelago.game_data.checksum import checksum
from crimsonipelago.tools import game_data_cli
from tests.conftest import editor_path


def test_checksum_known_values():
    assert checksum(b"") == 0xDEBA1DCD  # no blocks: the initial value
    assert checksum(b"hello") == checksum(b"hello") != checksum(b"world")
    # 12- and 13-byte inputs take different paths (full block vs. tail)
    assert checksum(b"a" * 12) != checksum(b"a" * 13)


def _game(tmp_path: Path, groups: dict[str, dict[str, bytes]], compression="lz4") -> Path:
    root = tmp_path / "Crimson Desert"
    entries = []
    for name, files in groups.items():
        pamt, chunks = ar.build_group(files, compression=compression)
        (root / name).mkdir(parents=True)
        (root / name / "0.pamt").write_bytes(pamt)
        for cid, data in chunks.items():
            (root / name / f"{cid}.paz").write_bytes(data)
        entries.append(ar.GroupEntry(name, 0, 0x3FFF, checksum(pamt[12:])))
    (root / "meta").mkdir()
    (root / "meta" / "0.papgt").write_bytes(ar.build_papgt(entries))
    return root


SKILL = b"skill-table-body" * 50


@pytest.mark.parametrize("compression", ["lz4", "zlib", "none"])
def test_build_then_read_roundtrip(tmp_path, compression):
    root = _game(tmp_path, {"0000": {"gamedata/binary__/client/bin/skill.pabgb": SKILL,
                                     "gamedata/binary__/client/bin/skill.pabgh": b"\x00\x00",
                                     "ui/readme.txt": b"hi"}}, compression)
    ga = ar.GameArchive(root)
    paths = [vf.path for vf in ga.files()]
    assert sorted(paths) == ["gamedata/binary__/client/bin/skill.pabgb", "gamedata/binary__/client/bin/skill.pabgh",
                             "ui/readme.txt"]
    vf = ga.files("skill.pabgb")[0]
    assert vf.compression == compression and vf.crypto == "none"
    assert ga.read(vf) == SKILL


def test_first_group_wins_like_the_game(tmp_path):
    path = "gamedata/binary__/client/bin/skill.pabgb"
    root = _game(tmp_path, {"0036": {path: b"modded"}, "0000": {path: b"vanilla"}})
    ga = ar.GameArchive(root)
    [vf] = ga.files("*skill*")
    assert vf.group == "0036" and ga.read(vf) == b"modded"
    assert [v.group for v in ga.files("*skill*", all_copies=True)] == ["0036", "0000"]


def test_damaged_index_is_rejected(tmp_path):
    root = _game(tmp_path, {"0000": {"a/b.txt": b"x"}})
    pamt = bytearray((root / "0000" / "0.pamt").read_bytes())
    pamt[-3] ^= 0xFF
    (root / "0000" / "0.pamt").write_bytes(bytes(pamt))
    with pytest.raises(ar.ArchiveError, match="checksum mismatch"):
        ar.GameArchive(root).files()


def test_chacha20_entries_decode():
    vf = ar.VirtualFile("0000", "gamedata/x/secret.pabgb", 0, 0, 5, 5, 0x30)  # crypto 3, no compression
    info = b"\x02\x0e\x61"
    stored = ar._pack_cipher(b"hello", info, vf.path)
    assert stored != b"hello"
    assert ar.decode_entry(stored, vf, info) == b"hello"


def _record(key, name, payload=b""):
    n = name.encode()
    return struct.pack("<II", key, len(n)) + n + payload


def _write_table(folder: Path, name: str, records: list[tuple[int, bytes]], suffixes=(".pabgb", ".pabgh")):
    folder.mkdir(parents=True, exist_ok=True)
    body, index = b"", []
    for key, data in records:
        index.append(struct.pack("<II", key, len(body)))
        body += data
    (folder / f"{name}{suffixes[0]}").write_bytes(body)
    (folder / f"{name}{suffixes[1]}").write_bytes(struct.pack("<H", len(index)) + b"".join(index))


def _table_files(folder: Path):
    _write_table(folder, "skill", [(15027, _record(15027, "Skill_CrowWing", struct.pack("<I", 40002))),
                                   (15036, _record(15036, "Skill_CrowWing_Start"))])
    _write_table(folder, "knowledgeinfo", [(40002, _record(40002, "Knowledge_CrowWing", struct.pack("<II", 15027, 75001)))])


def test_index_variants():
    assert tb.parse_index(struct.pack("<H", 1) + struct.pack("<II", 7, 0)) == [(7, 0)]
    assert tb.parse_index(struct.pack("<H", 1) + struct.pack("<HI", 7, 0)) == [(7, 0)]
    assert tb.parse_index(struct.pack("<H", 1) + struct.pack("<BI", 7, 0)) == [(7, 0)]
    with pytest.raises(tb.TableError):
        tb.parse_index(b"\x05\x00\x01")


def test_tables_search_and_references(tmp_path):
    _table_files(tmp_path)
    tables = tb.load_tables(tmp_path)
    assert {r.name for r in tables["skill"].records.values()} == {"Skill_CrowWing", "Skill_CrowWing_Start"}
    hits = tb.search(tables, 40002)
    assert [(t, r.key) for t, r, _ in hits] == [("skill", 15027)]  # the record's own key is not a hit
    names = tb.KeyNames(tables)
    refs = tb.references(tables["knowledgeinfo"].records[40002], names)
    assert (15027 in [v for _, v, _ in refs]) and names.describe(15027) == "skill:Skill_CrowWing"


def test_current_build_naming_is_recognised(tmp_path):
    _write_table(tmp_path, "skill", [(15027, _record(15027, "Skill_CrowWing", struct.pack("<I", 40002)))],
                 suffixes=(".staticinfobody", ".staticinfoheader"))
    _write_table(tmp_path, "knowledgeinfo", [(40002, _record(40002, "Knowledge_CrowWing"))])  # older naming
    tables = tb.load_tables(tmp_path)
    assert set(tables) == {"skill", "knowledgeinfo"}
    assert [(t, r.key) for t, r, _ in tb.search(tables, 40002)] == [("skill", 15027)]


def test_cli_end_to_end(tmp_path, capsys):
    root = _game(tmp_path, {"0000": {"gamedata/binary__/client/bin/skill.pabgb": SKILL}})
    out = tmp_path / "out"
    assert game_data_cli.main(["--game", str(root), "extract", "*skill*", "-o", str(out)]) == 0
    assert (out / "gamedata/binary__/client/bin/skill.pabgb").read_bytes() == SKILL
    assert game_data_cli.main(["--game", str(root), "extract", "*", "-o", str(out), "--limit", "0"]) == 1
    capsys.readouterr()
    assert game_data_cli.main(["--game", str(root), "types"]) == 0
    assert ".pabgb" in capsys.readouterr().out
    _table_files(tmp_path / "t")
    assert game_data_cli.main(["search", str(tmp_path / "t"), "40002"]) == 0
    assert "Skill_CrowWing" in capsys.readouterr().out


def test_missing_game_folder_message(tmp_path):
    with pytest.raises(SystemExit, match="Crimson Desert not found"):
        game_data_cli.find_game_dir(str(tmp_path))


EDITOR = editor_path()
PACK = EDITOR.parent / "CrimsonGameMods" / "packs" / "Universal Proficiency" if EDITOR else None


@pytest.mark.skipif(not (PACK and PACK.is_dir()), reason="NattKh mod pack not available (set NATTKH_EDITOR_DIR)")
def test_reads_a_real_mod_pack():
    """A pack built by the community tools and loaded by the real game: every checksum must verify."""
    ga = ar.GameArchive(PACK)
    assert ga.groups[0].name == "0041"
    files = ga.files("*")
    assert {vf.name for vf in files} >= {"iteminfo.pabgb", "equipslotinfo.pabgb", "equipslotinfo.pabgh"}
    pamt = ga.pamt("0041")
    paz = (PACK / "0041" / "0.paz").read_bytes()
    assert pamt.chunks[0] == (checksum(paz), len(paz))
    table = tb.Table("equipslotinfo", ga.read(ga.files("equipslotinfo.pabgb")[0]), ga.read(ga.files("equipslotinfo.pabgh")[0]))
    assert len(table) > 0
