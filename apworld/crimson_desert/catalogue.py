"""Crimson Desert game data catalogue: loading, validation and the rule language.

This module deliberately has no Archipelago imports so the client, the game
adapter and the tools can use exactly the same catalogue code the APWorld uses.

Rule language (JSON):
    null                                   -> always true
    {"item": "Name"}                       -> has the item
    {"item": "Name", "count": 2}           -> has at least 2 copies
    {"event": "Event Name"}                -> has the generation event item
    {"location": "Location Name"}          -> can reach that location
    {"all": [rule, ...]}                   -> every sub-rule is true
    {"any": [rule, ...]}                   -> at least one sub-rule is true
"""

from __future__ import annotations

import hashlib
import json
import pkgutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

SCHEMA_VERSION = 1
CATALOGUE_FILES = ("items", "locations", "regions", "progression", "compatibility")

CLASSIFICATIONS = {"progression", "useful", "filler", "trap"}
CAPABILITIES = {"supported", "restricted", "detection_only", "unsupported"}
DELIVERABLE_CAPABILITIES = {"supported", "restricted"}
LOCATION_CATEGORIES = {"quest", "boss", "discovery", "treasure", "ability", "exploration", "regional", "abyss_ruins"}
# on unless a player's options say otherwise (the later, optional categories are off by default)
DEFAULT_CATEGORIES = frozenset({"quest", "boss", "discovery", "treasure", "ability", "exploration"})
DETECTION_KINDS = {"placeholder", "quest_state", "mission_state", "knowledge"}
GAME_REF_KINDS = {"inventory_item", "knowledge", "quest_state", "nothing"}  # nothing: filler with no in-game effect
CATALOGUE_STATUSES = {"placeholder", "partial", "verified"}

Rule = Any  # JSON rule expression, see module docstring


class CatalogueError(ValueError):
    """Raised when catalogue data is malformed or internally inconsistent."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("Invalid Crimson Desert catalogue:\n  - " + "\n  - ".join(problems))


@dataclass(frozen=True)
class ItemDef:
    ap_id: int
    name: str
    classification: str
    category: str
    game_ref: Mapping[str, Any] | None
    quantity: int
    repeatable: bool
    starting_safe: bool
    capability: str
    verified: bool
    evidence: str | None
    game_versions: tuple[str, ...]
    slice: int = 1                  # the first slice whose seeds can hold it

    @property
    def deliverable(self) -> bool:
        """True when the adapter is allowed to try to grant this item."""
        return self.verified and self.capability in DELIVERABLE_CAPABILITIES and self.game_ref is not None

    @property
    def draft_deliverable(self) -> bool:
        """An unverified item of a kind the client delivers (knowledge, inventory items): playable in a seed with
        include_draft_slices, where the playthrough confirms it."""
        return self.deliverable or (self.game_ref is not None
                                    and self.game_ref.get("kind") in ("knowledge", "inventory_item", "nothing"))


@dataclass(frozen=True)
class LocationDef:
    ap_id: int
    name: str
    region: str
    category: str
    detection: Mapping[str, Any]
    requires: Rule
    missable: bool
    repeatable_risk: bool
    verified: bool
    evidence: str | None
    caveats: str | None
    no_progression: bool = False    # only ever holds filler (unsure when it's reachable, or missable)

    @property
    def detectable(self) -> bool:
        """True when the real adapter can detect this check from save data."""
        return self.verified and self.detection.get("kind") != "placeholder"

    @property
    def draft_detectable(self) -> bool:
        """Detectable once drafts are allowed: any real detection method, verified or not."""
        return self.detection.get("kind") != "placeholder"


@dataclass(frozen=True)
class EventDef:
    name: str
    region: str
    requires: Rule


@dataclass(frozen=True)
class GoalDef:
    key: str
    display: str
    region: str
    requires: Rule
    detection: Mapping[str, Any]
    verified: bool
    slice: int = 1                  # seeds with this goal cover slices 1..slice


@dataclass(frozen=True)
class Connection:
    source: str
    target: str
    requires: Rule


@dataclass
class Catalogue:
    raw: dict[str, Any]
    status: str
    items: dict[str, ItemDef]
    locations: dict[str, LocationDef]
    regions: list[str]
    origin: str
    region_slices: dict[str, int]
    connections: list[Connection]
    events: dict[str, EventDef]
    goals: dict[str, GoalDef]
    default_goal: str
    item_pools: dict[str, dict[str, int]]
    slice_pools: dict[int, dict[str, int]]
    filler_weights: dict[str, int]
    trap_weights: dict[str, int]
    compatibility: dict[str, Any]
    _by_item_id: dict[int, ItemDef] = field(default_factory=dict, repr=False)
    _by_location_id: dict[int, LocationDef] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._by_item_id = {i.ap_id: i for i in self.items.values()}
        self._by_location_id = {loc.ap_id: loc for loc in self.locations.values()}

    # ------------------------------------------------------------------ lookups
    @property
    def item_name_to_id(self) -> dict[str, int]:
        return {name: item.ap_id for name, item in self.items.items()}

    @property
    def location_name_to_id(self) -> dict[str, int]:
        return {name: loc.ap_id for name, loc in self.locations.items()}

    def item_by_id(self, ap_id: int) -> ItemDef | None:
        return self._by_item_id.get(ap_id)

    def location_by_id(self, ap_id: int) -> LocationDef | None:
        return self._by_location_id.get(ap_id)

    @property
    def is_placeholder(self) -> bool:
        return self.status == "placeholder"

    @property
    def fingerprint(self) -> str:
        """Stable hash of the catalogue content.

        Sent in slot_data so the client can refuse to sync a seed that was
        generated from a different catalogue than the one it has installed.
        """
        canonical = json.dumps(self.raw, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    # ------------------------------------------------------------------ slices
    # The world grows a slice at a time (a chapter or two). A seed covers the slices up to its goal's: the regions
    # of those slices, their checks, and the items that belong to them. Later slices can sit in the catalogue as
    # drafts (unverified) without touching earlier seeds.
    def goal_slice(self, goal_key: str) -> int:
        return self.goals[goal_key].slice

    def regions_in_scope(self, goal_key: str) -> list[str]:
        top = self.goal_slice(goal_key)
        return [r for r in self.regions if self.region_slices.get(r, 1) <= top]

    def locations_in_scope(self, goal_key: str) -> list[LocationDef]:
        regions = set(self.regions_in_scope(goal_key))
        return [loc for loc in self.locations.values() if loc.region in regions]

    def connections_in_scope(self, goal_key: str) -> list[Connection]:
        regions = set(self.regions_in_scope(goal_key))
        return [c for c in self.connections if c.source in regions and c.target in regions]

    def events_in_scope(self, goal_key: str) -> list[EventDef]:
        regions = set(self.regions_in_scope(goal_key))
        return [e for e in self.events.values() if e.region in regions]

    def items_in_scope(self, goal_key: str) -> list[ItemDef]:
        top = self.goal_slice(goal_key)
        return [i for i in self.items.values() if i.slice <= top]

    def scope_fingerprint(self, goal_key: str) -> str:
        """Hash of what a seed with this goal is made of: its regions, connections, checks, items, pools and goal.
        A later slice added as a draft doesn't change it, so earlier seeds keep working with a newer client."""
        top = self.goal_slice(goal_key)
        regions = set(self.regions_in_scope(goal_key))
        items = {i.name for i in self.items_in_scope(goal_key)}
        raw = self.raw
        scope = {
            "schema": raw["items"].get("schema_version"),
            "regions": [r for r in raw["regions"].get("regions", []) if r["name"] in regions],
            "origin": raw["regions"].get("origin"),
            "connections": [c for c in raw["regions"].get("connections", []) if c["from"] in regions
                            and c["to"] in regions],
            "locations": [loc for loc in raw["locations"].get("locations", []) if loc["region"] in regions],
            "items": [i for i in raw["items"].get("items", []) if i["name"] in items],
            "item_pool": {mode: {n: c for n, c in pool.items() if n in items}
                          for mode, pool in raw["items"].get("item_pool", {}).items()},
            "slice_pool": {k: v for k, v in raw["items"].get("slice_pool", {}).items()
                           if not k.startswith("_") and int(k) <= top},
            "filler_weights": raw["items"].get("filler_weights", {}),
            "trap_weights": raw["items"].get("trap_weights", {}),
            "events": [e for e in raw["progression"].get("events", []) if e["region"] in regions],
            "goal": {goal_key: raw["progression"].get("goals", {}).get(goal_key)},
            "slice": top,
        }
        canonical = json.dumps(scope, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    def pool_item_eligible(self, item: ItemDef, drafts: bool = False) -> bool:
        """Whether an item may be placed in the randomized pool.

        Unsupported or detection-only rewards must never be randomized
        (scope doc 6.5). A placeholder catalogue is exempt so that generation
        and the client can be developed before any real data exists; the
        client still refuses to deliver anything that is not verified.
        """
        if self.is_placeholder:
            return True
        return item.draft_deliverable if drafts else item.deliverable


# --------------------------------------------------------------------------- loading
def _read_package_json(name: str) -> dict[str, Any]:
    data = pkgutil.get_data(__package__ or "crimson_desert", f"data/{name}.json")
    if data is None:
        raise FileNotFoundError(f"catalogue file data/{name}.json not found in package")
    return json.loads(data.decode("utf-8"))


def load_raw(directory: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Load the raw catalogue JSON, from the package (default) or a directory."""
    raw: dict[str, dict[str, Any]] = {}
    for name in CATALOGUE_FILES:
        if directory is None:
            raw[name] = _read_package_json(name)
        else:
            raw[name] = json.loads((Path(directory) / f"{name}.json").read_text(encoding="utf-8"))
    return raw


_DEFAULT: Catalogue | None = None


def default_catalogue() -> Catalogue:
    """The catalogue shipped inside this package, loaded and validated once."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = load()
    return _DEFAULT


def load(directory: str | Path | None = None, *, validate: bool = True) -> Catalogue:
    raw = load_raw(directory)
    catalogue = build(raw)
    if validate:
        problems = validate_catalogue(catalogue)
        if problems:
            raise CatalogueError(problems)
    return catalogue


def build(raw: dict[str, dict[str, Any]]) -> Catalogue:
    items_doc, locs_doc = raw["items"], raw["locations"]
    regions_doc, prog_doc = raw["regions"], raw["progression"]

    items = {}
    for entry in items_doc.get("items", []):
        item = ItemDef(
            ap_id=int(entry["ap_id"]),
            name=entry["name"],
            classification=entry["classification"],
            category=entry.get("category", ""),
            game_ref=entry.get("game_ref"),
            quantity=int(entry.get("quantity", 1)),
            repeatable=bool(entry.get("repeatable", False)),
            starting_safe=bool(entry.get("starting_safe", False)),
            capability=entry.get("capability", "unsupported"),
            verified=bool(entry.get("verified", False)),
            evidence=entry.get("evidence"),
            game_versions=tuple(entry.get("game_versions", [])),
            slice=int(entry.get("slice", 1)),
        )
        items.setdefault(item.name, item)  # duplicates are reported by validation

    locations = {}
    for entry in locs_doc.get("locations", []):
        loc = LocationDef(
            ap_id=int(entry["ap_id"]),
            name=entry["name"],
            region=entry["region"],
            category=entry.get("category", ""),
            detection=entry.get("detection") or {"kind": "placeholder"},
            requires=entry.get("requires"),
            missable=bool(entry.get("missable", False)),
            repeatable_risk=bool(entry.get("repeatable_risk", False)),
            verified=bool(entry.get("verified", False)),
            evidence=entry.get("evidence"),
            caveats=entry.get("caveats"),
            no_progression=bool(entry.get("no_progression", False)),
        )
        locations.setdefault(loc.name, loc)

    events = {
        e["name"]: EventDef(name=e["name"], region=e["region"], requires=e.get("requires"))
        for e in prog_doc.get("events", [])
    }
    region_slices = {r["name"]: int(r.get("slice", 1)) for r in regions_doc.get("regions", [])}
    goals = {
        key: GoalDef(
            key=key,
            display=g.get("display", key),
            region=g["region"],
            requires=g.get("requires"),
            detection=g.get("detection") or {"kind": "placeholder"},
            verified=bool(g.get("verified", False)),
            slice=int(g.get("slice", region_slices.get(g["region"], 1))),
        )
        for key, g in prog_doc.get("goals", {}).items()
    }
    statuses = {doc.get("catalogue_status", "placeholder") for doc in (items_doc, locs_doc, regions_doc, prog_doc)}
    status = "placeholder" if "placeholder" in statuses else ("partial" if "partial" in statuses else "verified")

    cat = Catalogue(
        raw=raw,
        status=status,
        items=items,
        locations=locations,
        regions=[r["name"] for r in regions_doc.get("regions", [])],
        origin=regions_doc.get("origin", ""),
        region_slices=region_slices,
        connections=[
            Connection(source=c["from"], target=c["to"], requires=c.get("requires"))
            for c in regions_doc.get("connections", [])
        ],
        events=events,
        goals=goals,
        default_goal=prog_doc.get("default_goal", next(iter(goals), "")),
        item_pools={k: dict(v) for k, v in items_doc.get("item_pool", {}).items()},
        slice_pools={int(k): dict(v) for k, v in items_doc.get("slice_pool", {}).items() if not k.startswith("_")},
        filler_weights=dict(items_doc.get("filler_weights", {})),
        trap_weights=dict(items_doc.get("trap_weights", {})),
        compatibility=raw["compatibility"],
    )
    # keep duplicate detection possible: stash raw entry counts
    cat.raw["_entry_counts"] = {
        "items": len(items_doc.get("items", [])),
        "locations": len(locs_doc.get("locations", [])),
    }
    return cat


# --------------------------------------------------------------------------- rules
def rule_references(rule: Rule) -> Iterable[tuple[str, str]]:
    """Yield (kind, name) for every item / event / location a rule mentions."""
    if rule is None:
        return
    if not isinstance(rule, dict) or len(set(rule) - {"count"}) != 1:
        yield ("invalid", json.dumps(rule))
        return
    if "all" in rule or "any" in rule:
        subrules = rule.get("all", rule.get("any"))
        if not isinstance(subrules, list) or not subrules:
            yield ("invalid", json.dumps(rule))
            return
        for sub in subrules:
            yield from rule_references(sub)
    elif "item" in rule:
        yield ("item", rule["item"])
    elif "event" in rule:
        yield ("event", rule["event"])
    elif "location" in rule:
        yield ("location", rule["location"])
    else:
        yield ("invalid", json.dumps(rule))


def compile_rule(
    rule: Rule,
    has: Callable[[str, int], bool],
    can_reach_location: Callable[[str], bool],
) -> Callable[[], bool]:
    """Turn a JSON rule into a zero-argument predicate over the given accessors."""
    if rule is None:
        return lambda: True
    if "all" in rule:
        subs = [compile_rule(r, has, can_reach_location) for r in rule["all"]]
        return lambda: all(s() for s in subs)
    if "any" in rule:
        subs = [compile_rule(r, has, can_reach_location) for r in rule["any"]]
        return lambda: any(s() for s in subs)
    if "item" in rule:
        name, count = rule["item"], int(rule.get("count", 1))
        return lambda: has(name, count)
    if "event" in rule:
        name = rule["event"]
        return lambda: has(name, 1)
    if "location" in rule:
        name = rule["location"]
        return lambda: can_reach_location(name)
    raise CatalogueError([f"unknown rule {rule!r}"])


# --------------------------------------------------------------------------- validation
_DETECTION_KEYS = {"quest_state": "quest_key", "mission_state": "mission_key", "knowledge": "knowledge_key"}


def detection_problems(detection: Mapping[str, Any]) -> list[str]:
    """Shape of a detection rule. mission_state may name a parent mission whose completion also counts
    (an objective is done once its mission is, even if it was skipped)."""
    kind = detection.get("kind")
    key = _DETECTION_KEYS.get(kind)
    if key is None:
        return []
    problems = []
    if not isinstance(detection.get(key), int):
        problems.append(f"{kind} detection needs an integer {key}")
    if kind in ("quest_state", "mission_state"):
        states = detection.get("states")
        if not (isinstance(states, list) and states and all(isinstance(s, int) for s in states)):
            problems.append(f"{kind} detection needs a non-empty list of integer states")
    if "parent_mission_key" in detection and (kind != "mission_state" or not isinstance(detection["parent_mission_key"], int)):
        problems.append("parent_mission_key must be an integer, on a mission_state detection")
    return problems


def validate_catalogue(cat: Catalogue) -> list[str]:
    """Return a list of problems; empty means the catalogue is internally consistent."""
    problems: list[str] = []
    raw = cat.raw

    for doc_name in ("items", "locations", "regions", "progression", "compatibility"):
        version = raw[doc_name].get("schema_version")
        if version != SCHEMA_VERSION:
            problems.append(f"{doc_name}.json: schema_version {version!r}, expected {SCHEMA_VERSION}")

    if cat.status not in CATALOGUE_STATUSES:
        problems.append(f"unknown catalogue status {cat.status!r}")

    # --- identifiers
    counts = raw.get("_entry_counts", {})
    if counts.get("items", len(cat.items)) != len(cat.items):
        problems.append("duplicate item names")
    if counts.get("locations", len(cat.locations)) != len(cat.locations):
        problems.append("duplicate location names")
    for kind, ids in (("item", [i.ap_id for i in cat.items.values()]),
                      ("location", [loc.ap_id for loc in cat.locations.values()])):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            problems.append(f"duplicate {kind} ids: {dupes}")
        if any(i <= 0 for i in ids):
            problems.append(f"{kind} ids must be positive integers")
    retired_items = set(cat.compatibility.get("retired_item_ids", []))
    retired_locs = set(cat.compatibility.get("retired_location_ids", []))
    for item in cat.items.values():
        if item.ap_id in retired_items:
            problems.append(f"item {item.name!r} reuses retired id {item.ap_id}")
    for loc in cat.locations.values():
        if loc.ap_id in retired_locs:
            problems.append(f"location {loc.name!r} reuses retired id {loc.ap_id}")

    names_in_use = set(cat.items) | set(cat.locations) | set(cat.events) | set(cat.regions)
    if len(names_in_use) != len(cat.items) + len(cat.locations) + len(cat.events) + len(set(cat.regions)):
        problems.append("item, location, event and region names must not collide")

    # --- items
    for item in cat.items.values():
        where = f"item {item.name!r}"
        if item.classification not in CLASSIFICATIONS:
            problems.append(f"{where}: unknown classification {item.classification!r}")
        if item.capability not in CAPABILITIES:
            problems.append(f"{where}: unknown capability {item.capability!r}")
        if item.quantity < 1:
            problems.append(f"{where}: quantity must be >= 1")
        if item.game_ref is not None and item.game_ref.get("kind") not in GAME_REF_KINDS:
            problems.append(f"{where}: unknown game_ref kind {item.game_ref.get('kind')!r}")
        if item.game_ref is not None and item.game_ref.get("kind") == "knowledge" \
                and not isinstance(item.game_ref.get("knowledge_key"), int):
            problems.append(f"{where}: a knowledge game_ref needs an integer knowledge_key")
        if item.game_ref is not None and item.game_ref.get("kind") == "inventory_item":
            ref = item.game_ref
            if not isinstance(ref.get("item_key"), int) or not isinstance(ref.get("count", 1), int) or ref.get("count", 1) < 1:
                problems.append(f"{where}: an inventory_item game_ref needs an integer item_key and a count >= 1")
        if item.verified and not item.evidence:
            problems.append(f"{where}: verified entries need an evidence reference")
        if item.verified and item.game_ref is None:
            problems.append(f"{where}: verified entries need a game_ref")
        if item.capability in DELIVERABLE_CAPABILITIES and not item.verified:
            problems.append(f"{where}: capability {item.capability!r} requires verified=true")

    # --- locations
    regions = set(cat.regions)
    for loc in cat.locations.values():
        where = f"location {loc.name!r}"
        if loc.region not in regions:
            problems.append(f"{where}: unknown region {loc.region!r}")
        if loc.category not in LOCATION_CATEGORIES:
            problems.append(f"{where}: unknown category {loc.category!r}")
        kind = loc.detection.get("kind")
        if kind not in DETECTION_KINDS:
            problems.append(f"{where}: unknown detection kind {kind!r}")
        if loc.verified and kind == "placeholder":
            problems.append(f"{where}: verified entries need a real detection method")
        if loc.verified and not loc.evidence:
            problems.append(f"{where}: verified entries need an evidence reference")
        problems += [f"{where}: {p}" for p in detection_problems(loc.detection)]
        if loc.repeatable_risk and kind in ("mission_state",) and not loc.caveats:
            problems.append(f"{where}: repeatable checks must document their first-completion identifier")

    # --- regions and rules
    if cat.origin not in regions:
        problems.append(f"origin region {cat.origin!r} is not defined")
    if len(cat.regions) != len(regions):
        problems.append("duplicate region names")
    for conn in cat.connections:
        for end in (conn.source, conn.target):
            if end not in regions:
                problems.append(f"connection {conn.source!r}->{conn.target!r}: unknown region {end!r}")

    def check_rule(rule: Rule, where: str) -> None:
        for kind, name in rule_references(rule):
            if kind == "invalid":
                problems.append(f"{where}: invalid rule {name}")
            elif kind == "item" and name not in cat.items:
                problems.append(f"{where}: rule references unknown item {name!r}")
            elif kind == "event" and name not in cat.events:
                problems.append(f"{where}: rule references unknown event {name!r}")
            elif kind == "location" and name not in cat.locations:
                problems.append(f"{where}: rule references unknown location {name!r}")

    for conn in cat.connections:
        check_rule(conn.requires, f"connection {conn.source!r}->{conn.target!r}")
    for loc in cat.locations.values():
        check_rule(loc.requires, f"location {loc.name!r}")
    for event in cat.events.values():
        check_rule(event.requires, f"event {event.name!r}")
        if event.region not in regions:
            problems.append(f"event {event.name!r}: unknown region {event.region!r}")
    if not cat.goals:
        problems.append("at least one goal is required")
    for goal in cat.goals.values():
        check_rule(goal.requires, f"goal {goal.key!r}")
        problems += [f"goal {goal.key!r}: {p}" for p in detection_problems(goal.detection)]
        if goal.region not in regions:
            problems.append(f"goal {goal.key!r}: unknown region {goal.region!r}")
    if cat.default_goal not in cat.goals:
        problems.append(f"default_goal {cat.default_goal!r} is not a defined goal")

    # --- slices: a seed's goal covers its slice and everything before it
    if cat.region_slices.get(cat.origin, 1) != 1:
        problems.append(f"origin region {cat.origin!r} must be in slice 1")
    for conn in cat.connections:
        if cat.region_slices.get(conn.target, 1) < cat.region_slices.get(conn.source, 1):
            problems.append(f"connection {conn.source!r}->{conn.target!r} leads back into an earlier slice")
    for goal in cat.goals.values():
        if goal.slice < cat.region_slices.get(goal.region, 1):
            problems.append(f"goal {goal.key!r}: slice {goal.slice} doesn't include its region {goal.region!r}")
        if goal.verified:
            unverified = [loc.name for loc in cat.locations_in_scope(goal.key) if not loc.verified]
            if unverified:
                problems.append(f"goal {goal.key!r} is verified but its seeds would hold unverified checks: "
                                f"{unverified[:3]}{' ...' if len(unverified) > 3 else ''}")
    for item in cat.items.values():
        if item.slice < 1:
            problems.append(f"item {item.name!r}: slice must be >= 1")

    # --- pools
    for pool_name, pool in cat.item_pools.items():
        for name, count in pool.items():
            if name not in cat.items:
                problems.append(f"item_pool {pool_name!r}: unknown item {name!r}")
            elif count < 1:
                problems.append(f"item_pool {pool_name!r}: count for {name!r} must be >= 1")
            elif count > 1 and not cat.items[name].repeatable and cat.items[name].classification != "progression":
                problems.append(f"item_pool {pool_name!r}: {name!r} is not repeatable but appears {count} times")
        if sum(pool.values()) > len(cat.locations):
            problems.append(f"item_pool {pool_name!r} has more items than there are locations")
    for slice_no, pool in cat.slice_pools.items():
        for name, count in pool.items():
            if name not in cat.items:
                problems.append(f"slice_pool {slice_no}: unknown item {name!r}")
            elif count < 1:
                problems.append(f"slice_pool {slice_no}: count for {name!r} must be >= 1")
            elif cat.items[name].slice > slice_no:
                problems.append(f"slice_pool {slice_no}: {name!r} belongs to slice {cat.items[name].slice}")
    for weights_name, weights in (("filler_weights", cat.filler_weights), ("trap_weights", cat.trap_weights)):
        for name, weight in weights.items():
            if name not in cat.items:
                problems.append(f"{weights_name}: unknown item {name!r}")
            elif not cat.items[name].repeatable:
                problems.append(f"{weights_name}: {name!r} must be repeatable")
            if weight < 0:
                problems.append(f"{weights_name}: negative weight for {name!r}")
    if not any(w > 0 for w in cat.filler_weights.values()):
        problems.append("filler_weights needs at least one positive weight (Archipelago requires infinite filler)")

    # --- origin must offer at least one check reachable with nothing
    if not any(loc.region == cat.origin and loc.requires is None for loc in cat.locations.values()):
        problems.append(f"origin region {cat.origin!r} needs at least one check with no requirements")

    return problems
