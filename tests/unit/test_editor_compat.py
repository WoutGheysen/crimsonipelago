"""Cross-check our independent format code against the NattKh save editor.

Skipped unless NATTKH_EDITOR_DIR points at a CrimsonSaveEditor checkout.
This is how we know our reader/writer agrees with the community tooling that
has been tested against real saves, without vendoring its code.
"""

import pytest

from crimsonipelago.game_adapter import parc, save_container
from crimsonipelago.game_adapter.nattkh_backend import NattKhBackend
from tests.conftest import editor_path
from tests.fixtures.synthetic_save import make_blob

EDITOR = editor_path()
pytestmark = pytest.mark.skipif(EDITOR is None, reason="NATTKH_EDITOR_DIR not set")


@pytest.fixture(scope="module")
def editor():
    backend = NattKhBackend(EDITOR)
    import sys
    return backend._parser, sys.modules.get("save_crypto") or __import__("save_crypto")


def test_editor_reads_what_we_write(tmp_path, editor):
    parser, crypto = editor
    blob = make_blob()
    path = tmp_path / "save.save"
    path.write_bytes(save_container.encode(blob, save_container.new_header(2)))
    loaded = crypto.load_save_file(str(path))  # raises on HMAC mismatch
    assert bytes(loaded.decompressed_blob) == blob


def test_we_read_what_the_editor_writes(tmp_path, editor):
    parser, crypto = editor
    blob = make_blob(quest_state=6)
    path = tmp_path / "save.save"
    crypto.write_save_file(str(path), blob, save_container.new_header(2))
    assert save_container.decode(path.read_bytes()).blob == blob


def test_schema_and_toc_agree(editor):
    parser, _ = editor
    blob = make_blob()
    ours = parc.parse_structure(blob)
    theirs = parser.parse_schema(blob)
    assert ours.schema_end == theirs["schema_end"]
    assert ours.type_names() == [t.name for t in theirs["types"]]
    toc = parser.parse_toc(blob, theirs["schema_end"], [t.name for t in theirs["types"]])
    assert [(e.class_name, e.data_offset, e.data_size) for e in ours.toc] == \
           [(e.class_name, e.data_offset, e.data_size) for e in toc["entries"]]
