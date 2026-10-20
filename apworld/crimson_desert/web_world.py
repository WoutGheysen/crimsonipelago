from __future__ import annotations

from BaseClasses import Tutorial
from worlds.AutoWorld import WebWorld

from .options import option_groups
from .presets import option_presets


class CrimsonDesertWebWorld(WebWorld):
    game = "Crimson Desert"
    theme = "dirt"

    tutorials = [
        Tutorial(
            "Multiworld Setup Guide",
            "How to set up Crimson Desert (Crimsonipelago) for an Archipelago multiworld.",
            "English",
            "setup_en.md",
            "setup/en",
            ["Wout Gheysen"],
        )
    ]
    option_groups = option_groups
    options_presets = option_presets
