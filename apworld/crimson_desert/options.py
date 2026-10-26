"""Player YAML options.

Scope doc 4.7: only options backed by working functionality are exposed.
DeathLink, traps, starting loadouts and notification preferences are planned
but intentionally absent until the game adapter can actually honour them.
"""

from __future__ import annotations

from dataclasses import dataclass

from Options import Choice, DefaultOnToggle, OptionGroup, OptionSet, PerGameCommonOptions, Toggle

from .catalogue import DEFAULT_CATEGORIES, LOCATION_CATEGORIES


class Goal(Choice):
    """Victory condition.

    first_slice: speak with the woman in white (the prologue and chapter 1). Verified.
    chapter_2 ... chapter_12: the end of that chapter. journeys_end: the epilogue, the whole game.
    Every goal after first_slice is a draft (built from the game's data, not yet confirmed in play): it needs
    include_draft_slices.
    """

    display_name = "Goal"
    option_first_slice = 0
    option_chapter_2 = 2
    option_chapter_3 = 3
    option_chapter_4 = 4
    option_chapter_5 = 5
    option_chapter_6 = 6
    option_chapter_7 = 7
    option_chapter_8 = 8
    option_chapter_9 = 9
    option_chapter_10 = 10
    option_chapter_11 = 11
    option_chapter_12 = 12
    option_journeys_end = 13
    default = 0


class IncludeDraftSlices(Toggle):
    """Experimental: allow the chapters that are still drafts (every goal after first_slice).

    Their checks come straight from the game's data and are detected from your save like the verified ones, but
    nobody has played them through with the randomizer yet: a check may never fire, or a story ability may be
    needed earlier than the logic thinks. The client logs every draft check it sees, which is how they get verified.
    """

    display_name = "Include Draft Slices"


class DraftChecksKeepOthersSafe(DefaultOnToggle):
    """In a multiworld: draft checks never hold another player's progression items.

    A draft check that turns out not to fire would leave that player waiting (until the host sends the item). With
    this on, draft checks hold your own items of any kind and other players' useful items and filler; their
    progression goes to checks that have been confirmed in play. No effect when you play alone.
    """

    display_name = "Draft Checks Keep Others Safe"


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

    Valid values: quest, boss, discovery, treasure, ability, exploration, regional, abyss_ruins, abyss_challenges,
    standstone, sealed_artifacts, spires.
    regional: the regional quests (the Witches, Legendary Wolf...). abyss_ruins: the Abyss ruins of each region the
    seed reaches, about 100 per region. abyss_challenges: the Abyss puzzles (38, from chapter 2).
    standstone: the Standstone adventures and ruins puzzles (97), sealed_artifacts: the trials that open a Sealed
    Abyss Artifact (141), spires: the spires into the Abyss (8); these three only in whole-game seeds.
    All of these are drafts (need include_draft_slices), off by default, and only ever hold filler.
    Removing categories shortens the seed; generation fails if too few checks remain
    to hold the required progression.
    """

    display_name = "Included Check Categories"
    valid_keys = sorted(LOCATION_CATEGORIES)
    default = DEFAULT_CATEGORIES


@dataclass
class CrimsonDesertOptions(PerGameCommonOptions):
    goal: Goal
    include_draft_slices: IncludeDraftSlices
    draft_checks_keep_others_safe: DraftChecksKeepOthersSafe
    item_pool_mode: ItemPoolMode
    included_check_categories: IncludedCheckCategories


option_groups = [
    OptionGroup("Randomization", [ItemPoolMode, IncludedCheckCategories]),
    OptionGroup("Experimental", [IncludeDraftSlices, DraftChecksKeepOthersSafe]),
]
