"""Access rules, compiled from the catalogue's JSON rule language."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from BaseClasses import CollectionState

from .catalogue import Rule, compile_rule, rule_references
from .locations import VICTORY_EVENT
from .regions import connection_name

if TYPE_CHECKING:
    from .world import CrimsonDesertWorld

StateRule = Callable[[CollectionState], bool]


def make_state_rule(world: CrimsonDesertWorld, rule: Rule) -> StateRule:
    if rule is None:
        return lambda state: True
    player = world.player
    created = {loc.name for loc in world.enabled_location_defs}
    cat = world.catalogue

    # compile once; the predicate reads the state being evaluated from this cell
    current: list[CollectionState] = []

    def has(name: str, count: int) -> bool:
        return current[-1].has(name, player, count)

    def can_reach_location(name: str) -> bool:
        state = current[-1]
        if name in created:
            return state.can_reach_location(name, player)
        # the location's category is disabled, but the activity still exists in the game
        loc = cat.locations[name]
        return state.can_reach_region(loc.region, player) and make_state_rule(world, loc.requires)(state)

    predicate = compile_rule(rule, has, can_reach_location)

    def state_rule(state: CollectionState) -> bool:
        current.append(state)  # a stack, because location rules can evaluate nested rules
        try:
            return predicate()
        finally:
            current.pop()

    return state_rule


def set_all_rules(world: CrimsonDesertWorld) -> None:
    cat = world.catalogue

    for conn in cat.connections_in_scope(world.goal_key):
        entrance = world.get_entrance(connection_name(conn.source, conn.target))
        entrance.access_rule = make_state_rule(world, conn.requires)
        # entrance rules that look at other locations need indirect conditions
        for kind, name in rule_references(conn.requires):
            if kind == "location":
                world.multiworld.register_indirect_condition(world.get_region(cat.locations[name].region), entrance)

    for loc_def in world.enabled_location_defs:
        world.get_location(loc_def.name).access_rule = make_state_rule(world, loc_def.requires)

    for event in cat.events_in_scope(world.goal_key):
        world.get_location(event.name).access_rule = make_state_rule(world, event.requires)

    goal = cat.goals[world.goal_key]
    world.get_location(f"Goal: {goal.display}").access_rule = make_state_rule(world, goal.requires)
    world.multiworld.completion_condition[world.player] = lambda state: state.has(VICTORY_EVENT, world.player)
