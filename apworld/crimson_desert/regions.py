"""Region graph, built from the catalogue."""

from __future__ import annotations

from typing import TYPE_CHECKING

from BaseClasses import Region

if TYPE_CHECKING:
    from .world import CrimsonDesertWorld


def connection_name(source: str, target: str) -> str:
    return f"{source} -> {target}"


def create_and_connect_regions(world: CrimsonDesertWorld) -> None:
    cat = world.catalogue
    regions = {name: Region(name, world.player, world.multiworld) for name in cat.regions_in_scope(world.goal_key)}
    world.multiworld.regions += list(regions.values())
    for conn in cat.connections_in_scope(world.goal_key):
        regions[conn.source].connect(regions[conn.target], connection_name(conn.source, conn.target))
