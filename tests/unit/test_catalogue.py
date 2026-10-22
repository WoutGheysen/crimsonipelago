import copy

import pytest
from crimson_desert import catalogue as cat_mod


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
    again = cat_mod.load()
    assert again.fingerprint == catalogue.fingerprint
    assert len(catalogue.fingerprint) == 16


def _broken(mutate):
    raw = copy.deepcopy(cat_mod.load_raw())
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
