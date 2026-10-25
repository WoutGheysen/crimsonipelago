import copy

import pytest
from crimson_desert import catalogue as cat_mod

from tests.conftest import PLACEHOLDER_CATALOGUE


def test_default_catalogue_is_valid(catalogue):
    assert cat_mod.validate_catalogue(catalogue) == []
    assert catalogue.is_placeholder
    assert catalogue.origin in catalogue.regions


def test_ids_are_unique(catalogue):
    item_ids = [i.ap_id for i in catalogue.items.values()]
    loc_ids = [loc.ap_id for loc in catalogue.locations.values()]
    assert len(item_ids) == len(set(item_ids))
    assert len(loc_ids) == len(set(loc_ids))


def test_fingerprint_is_stable(catalogue):
    again = cat_mod.load(PLACEHOLDER_CATALOGUE)
    assert again.fingerprint == catalogue.fingerprint
    assert len(catalogue.fingerprint) == 16


def _broken(mutate):
    raw = copy.deepcopy(cat_mod.load_raw(PLACEHOLDER_CATALOGUE))
    mutate(raw)
    return cat_mod.validate_catalogue(cat_mod.build(raw))


def test_duplicate_item_id_detected():
    def m(raw):
        raw["items"]["items"][1]["ap_id"] = raw["items"]["items"][0]["ap_id"]
    assert any("duplicate item ids" in p for p in _broken(m))


def test_unknown_rule_reference_detected():
    def m(raw):
        raw["regions"]["connections"][0]["requires"] = {"item": "Nonexistent Thing"}
    assert any("unknown item 'Nonexistent Thing'" in p for p in _broken(m))


def test_invalid_rule_shape_detected():
    def m(raw):
        raw["regions"]["connections"][0]["requires"] = {"item": "Key Item", "event": "Act 3 Boss Defeated"}
    assert any("invalid rule" in p for p in _broken(m))


def test_verified_entry_needs_evidence_and_detection():
    def m(raw):
        raw["locations"]["locations"][0]["verified"] = True
    problems = _broken(m)
    assert any("need a real detection method" in p for p in problems)
    assert any("evidence" in p for p in problems)


def test_deliverable_capability_requires_verification():
    def m(raw):
        raw["items"]["items"][0]["capability"] = "supported"
    assert any("requires verified=true" in p for p in _broken(m))


def test_retired_ids_cannot_be_reused():
    def m(raw):
        raw["compatibility"]["retired_location_ids"] = [raw["locations"]["locations"][0]["ap_id"]]
    assert any("retired id" in p for p in _broken(m))


def test_origin_must_have_free_check():
    def m(raw):
        for loc in raw["locations"]["locations"]:
            if loc["region"] == "Prologue":
                loc["requires"] = {"item": "Key Item"}
    assert any("needs at least one check with no requirements" in p for p in _broken(m))


@pytest.mark.parametrize("rule,inventory,expected", [
    (None, {}, True),
    ({"item": "A"}, {"A": 1}, True),
    ({"item": "A", "count": 2}, {"A": 1}, False),
    ({"all": [{"item": "A"}, {"item": "B"}]}, {"A": 1}, False),
    ({"any": [{"item": "A"}, {"item": "B"}]}, {"B": 1}, True),
    ({"event": "E"}, {"E": 1}, True),
])
def test_compile_rule(rule, inventory, expected):
    fn = cat_mod.compile_rule(rule, lambda n, c: inventory.get(n, 0) >= c, lambda n: False)
    assert fn() is expected


def test_pool_eligibility_respects_capability(catalogue):
    # placeholder catalogue: everything is allowed in the pool for development
    assert all(catalogue.pool_item_eligible(i) for i in catalogue.items.values())
    # but once the catalogue claims to be verified, unsupported items are excluded
    catalogue_copy = copy.copy(catalogue)
    catalogue_copy.status = "verified"
    eligible = {i.name for i in catalogue.items.values() if catalogue_copy.pool_item_eligible(i)}
    assert eligible == {i.name for i in catalogue.items.values() if i.deliverable} == {"Flight"}


# --------------------------------------------------------------------------- the shipped catalogue
def test_shipped_catalogue_is_the_real_first_slice(real_catalogue):
    cat = real_catalogue
    assert cat_mod.validate_catalogue(cat) == []
    assert cat.status == "partial" and not cat.is_placeholder
    assert cat.default_goal == "first_slice" and cat.goals["first_slice"].verified
    first = cat.locations_in_scope("first_slice")
    assert len(first) == 42 and all(loc.detectable for loc in first)
    assert all(i.deliverable for i in cat.items_in_scope("first_slice"))
    assert cat.items["Flight"].game_ref == {"kind": "knowledge", "knowledge_key": 40002}


def test_shipped_slice_2_is_a_draft_that_first_slice_seeds_never_see(real_catalogue):
    cat = real_catalogue
    goal = cat.goals["chapter_2"]
    assert goal.slice == 2 and not goal.verified
    draft = [loc for loc in cat.locations_in_scope("chapter_2") if loc not in cat.locations_in_scope("first_slice")]
    assert len(draft) == 50 and not any(loc.verified for loc in draft)
    assert "Double Jump" not in {i.name for i in cat.items_in_scope("first_slice")}
    assert {r for r in cat.regions_in_scope("chapter_2")} - set(cat.regions_in_scope("first_slice")) == {
        "After the Jump", "Unexpected Gift", "Hernand in Chaos", "The End of Greed"}
    # a draft slice doesn't change what a first-slice seed is made of
    raw = copy.deepcopy(cat.raw)
    raw["locations"]["locations"] = [loc for loc in raw["locations"]["locations"]
                                     if loc["name"] in {x.name for x in cat.locations_in_scope("first_slice")}]
    raw["items"]["items"] = [i for i in raw["items"]["items"] if i["name"] != "Double Jump"]
    for pool in raw["items"]["item_pool"].values():
        pool.pop("Double Jump", None)
    trimmed = cat_mod.build({k: v for k, v in raw.items() if not k.startswith("_")})
    assert trimmed.scope_fingerprint("first_slice") == cat.scope_fingerprint("first_slice")
    assert cat.scope_fingerprint("chapter_2") != cat.scope_fingerprint("first_slice")


def test_a_verified_goal_cannot_cover_unverified_checks(real_catalogue):
    raw = copy.deepcopy({k: v for k, v in real_catalogue.raw.items() if not k.startswith("_")})
    raw["progression"]["goals"]["chapter_2"]["verified"] = True
    problems = cat_mod.validate_catalogue(cat_mod.build(raw))
    assert any("chapter_2" in p and "unverified checks" in p for p in problems)


def test_shipped_location_ids_are_derived_from_game_keys(real_catalogue):
    for loc in real_catalogue.locations.values():
        assert loc.detection["kind"] == "mission_state"
        assert loc.ap_id == 10_000_000_000 + loc.detection["mission_key"]


def test_detection_shape_is_checked():
    def m(raw):
        raw["locations"]["locations"][0]["detection"] = {"kind": "mission_state", "mission_key": "x", "states": []}
    problems = _broken(m)
    assert any("integer mission_key" in p for p in problems)
    assert any("non-empty list of integer states" in p for p in problems)
