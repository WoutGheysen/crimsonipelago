"""Player YAML options.

Scope doc 4.7: only options backed by working functionality are exposed.
DeathLink, traps, starting loadouts and notification preferences are planned
but intentionally absent until the game adapter can actually honour them.
"""

from __future__ import annotations

from dataclasses import dataclass

from Options import Choice, OptionGroup, OptionSet, PerGameCommonOptions

from .catalogue import LOCATION_CATEGORIES


class Goal(Choice):
    """Victory condition.

    main_story: complete the main story.
    Only goals with reliable in-game detection are offered.
    """

    display_name = "Goal"
    option_main_story = 0
    default = 0


class ItemPoolMode(Choice):
    """How many progression unlocks are randomized.

    conservative: fewer unlocks in the pool; safe unlocks that are left out are given at the start.
    expanded: every supported unlock is shuffled into the multiworld.
    """

    display_name = "Item Pool Mode"
    option_conservative = 0
    option_expanded = 1
    default = 1


class IncludedCheckCategories(OptionSet):
    """Which kinds of activities count as checks.

    Valid values: quest, boss, discovery, treasure, ability, exploration.
    Removing categories shortens the seed; generation fails if too few checks remain
    to hold the required progression.
    """

    display_name = "Included Check Categories"
    valid_keys = sorted(LOCATION_CATEGORIES)
    default = frozenset(LOCATION_CATEGORIES)


@dataclass
class CrimsonDesertOptions(PerGameCommonOptions):
    goal: Goal
    item_pool_mode: ItemPoolMode
    included_check_categories: IncludedCheckCategories


option_groups = [
    OptionGroup("Randomization", [ItemPoolMode, IncludedCheckCategories]),
]
