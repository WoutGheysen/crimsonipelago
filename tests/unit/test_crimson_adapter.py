"""The real save adapter end to end on a synthetic save: identity, and delivering Flight.

Skipped unless NATTKH_EDITOR_DIR points at a CrimsonSaveEditor checkout.
"""

import pytest

from crimsonipelago.game_adapter import crimson_adapter, save_container
from crimsonipelago.game_adapter.crimson_adapter import CrimsonDesertSaveAdapter
from crimsonipelago.game_adapter.interface import DeliveryBatch, DeliveryEntry, GameVersion
from crimsonipelago.game_adapter.nattkh_backend import NattKhBackend
from tests.conftest import editor_path
from tests.fixtures.synthetic_save import make_knowledge_blob

EDITOR = editor_path()
pytestmark = pytest.mark.skipif(EDITOR is None, reason="NATTKH_EDITOR_DIR not set")

QUESTS = tuple((900 + i, 5, 4000 + 37 * i) for i in range(6))


@pytest.fixture
def setup(tmp_path, catalogue, monkeypatch):
    save = tmp_path / "saves" / "12345" / "slot0" / "save.save"
    save.parent.mkdir(parents=True)
    save.write_bytes(save_container.encode(make_knowledge_blob(quests=QUESTS), save_container.new_header(2)))
    adapter = CrimsonDesertSaveAdapter(catalogue, save_path=save, editor_dir=EDITOR, backup_dir=tmp_path / "backups",
                                       campaigns_file=tmp_path / "campaigns.json")
    monkeypatch.setattr(crimson_adapter, "game_running", lambda: False)
    adapter._version = GameVersion("25477059", "test build", supported=True, writes_allowed=True)
    return adapter, save


def knowledge(save):
    return NattKhBackend(EDITOR).decode(save_container.decode(save.read_bytes()).blob).knowledge


def test_flight_is_deliverable(setup, catalogue):
    adapter, _ = setup
    flight = catalogue.items["Flight"]
    assert flight.deliverable and flight.game_ref == {"kind": "knowledge", "knowledge_key": 40002}
    assert adapter.can_grant_item(flight).ok


def test_deliver_flight(setup, catalogue, tmp_path):
    adapter, save = setup
    batch = DeliveryBatch("b1" * 16, [DeliveryEntry(0, catalogue.items["Flight"])])
    result = adapter.grant_item(batch)
    assert result.ok, result.message
    assert 40002 in knowledge(save)
    assert list((tmp_path / "backups").rglob("*.save"))          # the original was backed up first
    # delivering again changes nothing and still reports success
    before = save.read_bytes()
    again = adapter.prepare(DeliveryBatch("b2" * 16, [DeliveryEntry(1, catalogue.items["Flight"])]))
    assert again.temp_path is None and again.pre_fingerprint == again.post_fingerprint
    assert adapter.commit(again).ok
    assert save.read_bytes() == before


def test_no_write_while_the_game_runs(setup, catalogue, monkeypatch):
    adapter, save = setup
    monkeypatch.setattr(crimson_adapter, "game_running", lambda: True)
    before = save.read_bytes()
    result = adapter.grant_item(DeliveryBatch("b3" * 16, [DeliveryEntry(0, catalogue.items["Flight"])]))
    assert not result.ok and "running" in result.message
    assert save.read_bytes() == before


def test_identity_is_the_campaign_not_the_slot(setup, tmp_path):
    adapter, save = setup
    first = adapter.get_save_identity()
    assert first is not None and "/campaign-" in first.profile_id
    # the same playthrough saved into another slot is the same campaign
    other_slot = tmp_path / "saves" / "12345" / "slot101" / "save.save"
    other_slot.parent.mkdir(parents=True)
    other_slot.write_bytes(save.read_bytes())
    adapter._save_override = other_slot
    assert adapter.get_save_identity().profile_id == first.profile_id


def test_too_early_save_has_no_identity_yet(tmp_path, catalogue):
    save = tmp_path / "save.save"
    save.write_bytes(save_container.encode(make_knowledge_blob(), save_container.new_header(2)))
    adapter = CrimsonDesertSaveAdapter(catalogue, save_path=save, editor_dir=EDITOR, backup_dir=tmp_path / "b",
                                       campaigns_file=tmp_path / "c.json")
    assert adapter.get_save_identity() is None
    assert "enough history" in adapter.identity_problem


def test_warns_when_the_flight_override_is_missing(setup, tmp_path, monkeypatch):
    adapter, _ = setup
    game = tmp_path / "game"
    (game / "meta").mkdir(parents=True)
    (game / "meta" / "0.papgt").write_bytes(b"")
    monkeypatch.setenv("CRIMSON_DESERT_DIR", str(game))
    warnings = adapter.setup_warnings()
    assert len(warnings) == 1 and "crimson-mod install flight-grant-removed" in warnings[0]
    group = game / "0036"
    group.mkdir()
    (group / "crimsonipelago_overlay.json").write_text('{"patch": "flight-grant-removed"}')
    assert adapter.setup_warnings() == []
