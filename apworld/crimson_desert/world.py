from __future__ import annotations

import json
import logging
import pkgutil
from collections.abc import Mapping
from typing import Any, ClassVar

from Options import OptionError
from worlds.AutoWorld import World

from . import catalogue as catalogue_module
from . import items, locations, regions, rules
from .catalogue import Catalogue, LocationDef
from .options import CrimsonDesertOptions
from .web_world import CrimsonDesertWebWorld

logger = logging.getLogger("Crimson Desert")

CATALOGUE: Catalogue = catalogue_module.default_catalogue()
_MANIFEST = json.loads((pkgutil.get_data(__name__, "archipelago.json") or b"{}").decode("utf-8"))
WORLD_VERSION: str = _MANIFEST.get("world_version", "0.0.0")
SLOT_DATA_VERSION = 1


def _groups(attr: str, defs) -> dict[str, set[str]]:
    groups: dict[str, set[str]] = {}
    for d in defs:
        key = getattr(d, attr)
        if key:
            groups.setdefault(key.replace("_", " ").title(), set()).add(d.name)
    return groups


class CrimsonDesertWorld(World):
    """
    Crimson Desert is an open-world action-adventure set on the continent of Pywel.
    Crimsonipelago turns its quests, bosses and discoveries into Archipelago checks.
    """

    game = "Crimson Desert"
    web = CrimsonDesertWebWorld()
    options_dataclass = CrimsonDesertOptions
    options: CrimsonDesertOptions

    catalogue: ClassVar[Catalogue] = CATALOGUE
    item_name_to_id = CATALOGUE.item_name_to_id
    location_name_to_id = CATALOGUE.location_name_to_id
    item_name_groups = _groups("category", CATALOGUE.items.values())
    location_name_groups = {
        **_groups("category", CATALOGUE.locations.values()),
        **{region: {loc.name for loc in CATALOGUE.locations.values() if loc.region == region}
           for region in CATALOGUE.regions},
    }
    origin_region_name = CATALOGUE.origin

    goal_key: str
    enabled_location_defs: list[LocationDef]
    logic_item_names: set[str]
    planned_pool: tuple[list[str], list[str]]

    def generate_early(self) -> None:
        cat = self.catalogue
        self.goal_key = self.options.goal.current_key
        if self.goal_key not in cat.goals:
            raise OptionError(f"Crimson Desert: goal {self.goal_key!r} is not available in this catalogue")
        if cat.is_placeholder:
            logger.warning(
                "Crimson Desert (player %s): generating from the PLACEHOLDER catalogue. "
                "This seed is for development and testing only; no check or item maps to real game data.",
                self.player_name,
            )

        self.enabled_location_defs = locations.enabled_locations(self)
        if not any(loc.region == cat.origin and loc.requires is None for loc in self.enabled_location_defs):
            raise OptionError(
                "Crimson Desert: the enabled check categories leave no check available at the start. "
                "Enable more categories in included_check_categories."
            )
        self.logic_item_names = items.required_progression_names(self)
        self.planned_pool = items.plan_item_pool(self, len(self.enabled_location_defs))

    def create_regions(self) -> None:
        regions.create_and_connect_regions(self)
        locations.create_all_locations(self)

    def set_rules(self) -> None:
        rules.set_all_rules(self)

    def create_items(self) -> None:
        items.create_all_items(self)

    def create_item(self, name: str) -> items.CrimsonDesertItem:
        if name in self.catalogue.items:
            return items.create_item(self, name)
        return items.create_event_item(self, name)

    def get_filler_item_name(self) -> str:
        return items.random_filler_name(self)

    def fill_slot_data(self) -> Mapping[str, Any]:
        return {
            "slot_data_version": SLOT_DATA_VERSION,
            "world_version": WORLD_VERSION,
            "catalogue_fingerprint": self.catalogue.fingerprint,
            "catalogue_status": self.catalogue.status,
            "goal": self.goal_key,
            "item_pool_mode": self.options.item_pool_mode.current_key,
            "included_check_categories": sorted(self.options.included_check_categories.value),
            "death_link": False,
        }
