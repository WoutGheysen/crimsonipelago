"""Recommended option presets (scope doc 7.5).

Chaos is intentionally missing: it is only added once Standard has been
proven stable in real playthroughs.
"""

from __future__ import annotations

from typing import Any

option_presets: dict[str, dict[str, Any]] = {
    "Casual": {
        "item_pool_mode": "conservative",
        "progression_balancing": 75,
        "accessibility": "full",
    },
    "Standard": {
        "item_pool_mode": "expanded",
        "progression_balancing": 50,
        "accessibility": "full",
    },
}
