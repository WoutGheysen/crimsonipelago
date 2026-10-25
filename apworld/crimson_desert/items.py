"""Item registry and item pool construction."""

from __future__ import annotations

from typing import TYPE_CHECKING

from BaseClasses import Item, ItemClassification
from Options import OptionError

from .catalogue import ItemDef, default_catalogue, rule_references

if TYPE_CHECKING:
    from .world import CrimsonDesertWorld

GAME = "Crimson Desert"

_CLASSIFICATION = {
    "progression": ItemClassification.progression,
    "useful": ItemClassification.useful,
    "filler": ItemClassification.filler,
    "trap": ItemClassification.trap,
}


class CrimsonDesertItem(Item):
    game = GAME


def classification_of(item: ItemDef) -> ItemClassification:
    return _CLASSIFICATION[item.classification]


def create_item(world: CrimsonDesertWorld, name: str) -> CrimsonDesertItem:
    item = world.catalogue.items[name]
    classification = classification_of(item)
    if name in ALL_LOGIC_ITEM_NAMES:
        # anything a rule depends on must be progression or the fill cannot see it
        classification |= ItemClassification.progression
    return CrimsonDesertItem(name, classification, item.ap_id, world.player)


def create_event_item(world: CrimsonDesertWorld, name: str) -> CrimsonDesertItem:
    return CrimsonDesertItem(name, ItemClassification.progression, None, world.player)


def required_progression_names(world: CrimsonDesertWorld) -> set[str]:
    """Every item that any active rule depends on (the rules of the slices this seed covers)."""
    return {name for rule in _scope_rules(world) for kind, name in rule_references(rule) if kind == "item"}


def _scope_rules(world: CrimsonDesertWorld) -> list:
    cat, goal = world.catalogue, world.goal_key
    return ([c.requires for c in cat.connections_in_scope(goal)]
            + [loc.requires for loc in cat.locations_in_scope(goal)]
            + [e.requires for e in cat.events_in_scope(goal)] + [cat.goals[goal].requires])


def _all_rules(cat):
    return ([c.requires for c in cat.connections] + [loc.requires for loc in cat.locations.values()]
            + [e.requires for e in cat.events.values()] + [g.requires for g in cat.goals.values()])


def logic_item_names(cat) -> frozenset[str]:
    """Items any rule in the catalogue depends on, for any goal or option.

    Used for classification, which must also work for item-link group worlds
    that never run generate_early.
    """
    return frozenset(name for rule in _all_rules(cat) for kind, name in rule_references(rule) if kind == "item")


def required_counts(world: CrimsonDesertWorld) -> dict[str, int]:
    """Highest copy count any single rule asks for, per item."""
    counts: dict[str, int] = {}

    def walk(rule) -> None:
        if not rule:
            return
        for key in ("all", "any"):
            if key in rule:
                for sub in rule[key]:
                    walk(sub)
                return
        if "item" in rule:
            counts[rule["item"]] = max(counts.get(rule["item"], 0), int(rule.get("count", 1)))

    for rule in _scope_rules(world):
        walk(rule)
    return counts


def plan_item_pool(world: CrimsonDesertWorld, location_count: int) -> tuple[list[str], list[str]]:
    """Decide (pool item names, precollected item names) for this slot.

    Raises OptionError when the chosen options cannot produce a completable world.
    """
    cat = world.catalogue
    mode = world.options.item_pool_mode.current_key
    wanted = cat.item_pools.get(mode)
    if wanted is None:
        raise OptionError(f"Crimson Desert: catalogue has no item pool for mode {mode!r}")

    in_scope = {i.name for i in cat.items_in_scope(world.goal_key)}
    drafts = getattr(world, "drafts", False)
    wanted = dict(wanted)
    for slice_no, extra in sorted(cat.slice_pools.items()):
        if slice_no <= cat.goal_slice(world.goal_key):
            for name, count in extra.items():
                wanted[name] = wanted.get(name, 0) + count
    pool: list[str] = []
    for name, count in wanted.items():
        if name in in_scope and cat.pool_item_eligible(cat.items[name], drafts):
            pool.extend([name] * count)

    precollected: list[str] = []
    for name in sorted(required_progression_names(world) - set(pool)):
        item = cat.items[name]
        if item.starting_safe and cat.pool_item_eligible(item, drafts):
            precollected.append(name)
        else:
            raise OptionError(
                f"Crimson Desert: logic requires {name!r}, but it is neither in the {mode!r} pool "
                f"nor safe to give as starting inventory"
            )

    for kind_name, needed in required_counts(world).items():
        have = pool.count(kind_name) + precollected.count(kind_name)
        if have < needed:
            raise OptionError(
                f"Crimson Desert: logic needs {needed}x {kind_name!r} but the {mode!r} pool only has {have}"
            )

    if len(pool) > location_count:
        # drop non-logic extras first; anything logic depends on is never dropped
        removable = [n for n in pool
                     if cat.items[n].classification != "progression" and n not in world.logic_item_names]
        while len(pool) > location_count and removable:
            pool.remove(removable.pop())
        if len(pool) > location_count:
            raise OptionError(
                f"Crimson Desert: {len(pool)} required items but only {location_count} checks are enabled. "
                f"Enable more check categories or use item_pool_mode: conservative."
            )
    return pool, precollected


def create_all_items(world: CrimsonDesertWorld) -> None:
    pool_names, precollected = world.planned_pool
    for name in precollected:
        world.push_precollected(world.create_item(name))
    itempool = [world.create_item(name) for name in pool_names]
    unfilled = len(world.multiworld.get_unfilled_locations(world.player))
    itempool += [world.create_filler() for _ in range(unfilled - len(itempool))]
    world.multiworld.itempool += itempool


def random_filler_name(world: CrimsonDesertWorld) -> str:
    weights = {n: w for n, w in world.catalogue.filler_weights.items() if w > 0}
    names = sorted(weights)
    return world.random.choices(names, weights=[weights[n] for n in names])[0]


ALL_LOGIC_ITEM_NAMES = logic_item_names(default_catalogue())
