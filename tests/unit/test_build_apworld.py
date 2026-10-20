"""crimson-build-apworld packaging."""

import json
import zipfile

from crimsonipelago.tools import build_apworld


def test_build_contains_world_and_skips_caches(tmp_path):
    src = tmp_path / "crimson_desert"
    (src / "data").mkdir(parents=True)
    (src / "test" / "__pycache__").mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (src / "archipelago.json").write_text('{"game": "Crimson Desert", "world_version": "0.1.0"}')
    (src / "data" / "items.json").write_text("{}")
    (src / "test" / "test_x.py").write_text("")
    (src / "test" / "__pycache__" / "test_x.cpython-313.pyc").write_bytes(b"x")
    (src / "stray.pyc").write_bytes(b"x")
    (src / ".apignore").write_text("test/__pycache__/\n")

    out = build_apworld.build(src, tmp_path / "out" / "crimson_desert.apworld")
    names = zipfile.ZipFile(out).namelist()
    assert names == [
        "crimson_desert/__init__.py",
        "crimson_desert/archipelago.json",
        "crimson_desert/data/items.json",
        "crimson_desert/test/test_x.py",
    ]


def test_packaged_manifest_has_container_fields(tmp_path):
    out = build_apworld.build(build_apworld.DEFAULT_SOURCE, tmp_path / "crimson_desert.apworld")
    manifest = json.loads(zipfile.ZipFile(out).read("crimson_desert/archipelago.json"))
    assert manifest["game"] == "Crimson Desert"
    assert manifest["compatible_version"] == 7 and manifest["version"] == 7
    assert manifest["world_version"]


def test_build_is_reproducible(tmp_path):
    a = build_apworld.build(build_apworld.DEFAULT_SOURCE, tmp_path / "a.apworld")
    b = build_apworld.build(build_apworld.DEFAULT_SOURCE, tmp_path / "b.apworld")
    assert a.read_bytes() == b.read_bytes()
    names = zipfile.ZipFile(a).namelist()
    assert "crimson_desert/data/items.json" in names
    assert "crimson_desert/archipelago.json" in names
    assert not any("__pycache__" in n for n in names)
