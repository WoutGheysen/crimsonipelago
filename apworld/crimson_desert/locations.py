"""Location registry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from BaseClasses import Location

from .catalogue import LocationDef
from .items import GAME, create_event_item

if TYPE_CHECKING:
    from .world import CrimsonDesertWorld

VICTORY_EVENT = "Victory"


class CrimsonDesertLocation(Location):
    game = GAME


def enabled_locations(world: CrimsonDesertWorld) -> list[LocationDef]:
    categories = world.options.included_check_categories.value
    return [loc for loc in world.catalogue.locations_in_scope(world.goal_key) if loc.category in categories]


def create_all_locations(world: CrimsonDesertWorld) -> None:
    for loc_def in world.enabled_location_defs:
        region = world.get_region(loc_def.region)
        region.locations.append(CrimsonDesertLocation(world.player, loc_def.name, loc_def.ap_id, region))

    for event in world.catalogue.events_in_scope(world.goal_key):
        region = world.get_region(event.region)
        location = CrimsonDesertLocation(world.player, event.name, None, region)
        location.place_locked_item(create_event_item(world, event.name))
        region.locations.append(location)

    goal = world.catalogue.goals[world.goal_key]
    goal_region = world.get_region(goal.region)
    victory = CrimsonDesertLocation(world.player, f"Goal: {goal.display}", None, goal_region)
    victory.place_locked_item(create_event_item(world, VICTORY_EVENT))
    goal_region.locations.append(victory)
