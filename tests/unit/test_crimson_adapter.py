"""The real save adapter end to end on a synthetic save: identity, and delivering Flight.

Skipped unless NATTKH_EDITOR_DIR points at a CrimsonSaveEditor checkout.
"""

import json

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
    assert adapter.setup_warnings() == []                 # checked per seed, once connected
    warnings = adapter.override_warnings(["Flight"])
    assert len(warnings) == 1 and "crimson-mod install flight-grant-removed" in warnings[0]
    assert "Flight" in warnings[0]
    group = game / "0036"
    group.mkdir()
    (group / "crimsonipelago_overlay.json").write_text('{"patch": "flight-grant-removed"}')
    assert adapter.override_warnings(["Flight"]) == []


# --------------------------------------------------------------------------- inventory items
from crimson_desert.catalogue import ItemDef  # noqa: E402

from crimsonipelago.client.database import DeliveryState  # noqa: E402
from crimsonipelago.client.item_delivery import DeliveryEngine  # noqa: E402
from tests.fixtures.synthetic_save import make_inventory_blob  # noqa: E402


def _item(ap_id, name, item_key, count, max_stack=None):
    return ItemDef(ap_id, name, "useful", "resource",
                   {"kind": "inventory_item", "item_key": item_key, "count": count, "max_stack": max_stack},
                   1, True, True, "supported", True, "test", ())


def test_deliver_items_and_park_what_does_not_fit(tmp_path, catalogue, monkeypatch, ledger, bus):
    save = tmp_path / "saves" / "1" / "slot0" / "save.save"
    save.parent.mkdir(parents=True)
    save.write_bytes(save_container.encode(make_inventory_blob(), save_container.new_header(2)))
    copper, artifacts = _item(9001, "100 Copper", 1, 100), _item(9002, "8 Abyss Artifacts", 75001, 8, 10)
    catalogue.items.update({copper.name: copper, artifacts.name: artifacts})
    catalogue._by_item_id.update({copper.ap_id: copper, artifacts.ap_id: artifacts})
    adapter = CrimsonDesertSaveAdapter(catalogue, save_path=save, editor_dir=EDITOR, backup_dir=tmp_path / "b",
                                       campaigns_file=tmp_path / "c.json")
    monkeypatch.setattr(crimson_adapter, "game_running", lambda: False)
    adapter._version = GameVersion("25477059", "test build", supported=True, writes_allowed=True)

    sid = ledger.get_or_create_session("SEED", 0, 1, "Wout", None, catalogue.fingerprint, "0.1.0").id
    for idx, item in enumerate((catalogue.items["Flight"], copper, artifacts)):
        ledger.record_received(sid, idx, item.ap_id, 100 + idx, 2, 1)
    engine = DeliveryEngine(ledger, adapter, catalogue, bus)
    engine.validate(sid)
    report = engine.deliver_pending(sid)

    states = [r.state for r in ledger.items(sid)]
    assert states == [DeliveryState.COMPLETE, DeliveryState.COMPLETE, DeliveryState.PENDING]
    assert report.delivered == 2 and report.pending == 1
    assert "stack is full" in ledger.items(sid)[2].last_error
    state = NattKhBackend(EDITOR).decode(save_container.decode(save.read_bytes()).blob)
    assert 40002 in state.knowledge and state.items[1] == 100 and state.items[75001] == 3
    # nothing changed: the next attempt answers from the cache instead of decoding the save again
    assert engine.deliver_pending(sid).pending == 1


# --------------------------------------------------------------------------- the seed's override
from crimsonipelago.game_adapter.crimson_adapter import best_override, story_items_of  # noqa: E402


def _game_with(tmp_path, monkeypatch, patch=None):
    game = tmp_path / "game"
    (game / "meta").mkdir(parents=True, exist_ok=True)
    (game / "meta" / "0.papgt").write_bytes(b"")
    if patch:
        (game / "0036").mkdir(exist_ok=True)
        (game / "0036" / "crimsonipelago_overlay.json").write_text(json.dumps({"patch": patch}))
    monkeypatch.setenv("CRIMSON_DESERT_DIR", str(game))


def test_story_items_of_older_seeds_are_flight_and_double_jump_with_drafts():
    assert story_items_of({}, 1) == ["Flight"]
    assert story_items_of({"include_draft_slices": True}, 13) == ["Flight", "Double Jump"]
    assert story_items_of({"story_items": ["Back Hang", "Flight"]}, 13) == ["Back Hang", "Flight"]


def test_best_override_matches_the_seed_exactly():
    assert best_override(frozenset({"Flight"})) == "flight-grant-removed"
    assert best_override(frozenset({"Flight", "Double Jump"})) == "story-grants-removed"
    assert best_override(frozenset({"Flight", "Double Jump", "Call Dragon", "Back Hang", "Aerial Maneuver",
                                    "Focused Force Palm"})) == "story-abilities-removed"
    assert best_override(frozenset()) is None


def test_a_seed_with_the_later_abilities_asks_for_the_bigger_override(setup, tmp_path, monkeypatch):
    adapter, _ = setup
    from crimson_desert.catalogue import default_catalogue
    adapter.catalogue = default_catalogue()
    six = ["Flight", "Double Jump", "Focused Force Palm", "Back Hang", "Aerial Maneuver", "Call Dragon"]
    _game_with(tmp_path, monkeypatch, "story-grants-removed")
    warning = adapter.override_warnings(six)
    assert len(warning) == 1 and "crimson-mod uninstall, then crimson-mod install story-abilities-removed" in warning[0]


def test_a_seed_without_them_warns_when_the_bigger_override_would_take_them_away(setup, tmp_path, monkeypatch):
    adapter, _ = setup
    from crimson_desert.catalogue import default_catalogue
    adapter.catalogue = default_catalogue()
    _game_with(tmp_path, monkeypatch, "story-abilities-removed")
    warning = adapter.override_warnings(["Flight", "Double Jump"])        # a seed like the one of 25 Oct
    assert len(warning) == 1 and "you'd never get them" in warning[0]
    assert "install story-grants-removed" in warning[0] and "Back Hang" in warning[0]
    _game_with(tmp_path, monkeypatch, "story-grants-removed")
    (tmp_path / "game" / "0036" / "crimsonipelago_overlay.json").write_text('{"patch": "story-grants-removed"}')
    assert adapter.override_warnings(["Flight", "Double Jump"]) == []


def test_a_seed_that_ends_before_call_dragon_is_fine_with_the_bigger_override(setup, tmp_path, monkeypatch):
    adapter, _ = setup
    from crimson_desert.catalogue import default_catalogue
    adapter.catalogue = default_catalogue()
    _game_with(tmp_path, monkeypatch, "story-abilities-removed")
    chapter_9 = ["Flight", "Double Jump", "Focused Force Palm", "Back Hang", "Aerial Maneuver"]
    assert adapter.override_warnings(chapter_9, 9) == []
    assert "Call Dragon" in adapter.override_warnings(chapter_9, 13)[0]
