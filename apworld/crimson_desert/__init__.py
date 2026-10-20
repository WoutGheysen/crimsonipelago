"""Crimson Desert APWorld (Crimsonipelago).

The World class is only imported when running inside Archipelago, so the
catalogue module can also be used by the standalone client and tools.
"""

try:
    from worlds.AutoWorld import World as _World  # noqa: F401
except ImportError:  # imported outside Archipelago (client, tools, tests)
    pass
else:
    from .world import CrimsonDesertWorld as CrimsonDesertWorld
