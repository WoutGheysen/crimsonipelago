"""Which game-data override a seed needs, whether the installed one fits, and switching to the right one.

Some abilities the story teaches would make their Archipelago item pointless, so a game-data override (crimson-mod,
`game_data/patches/`) takes them out of the story. Only one override is installed at a time, and it has to match the
seed: too small and the story still teaches an ability the seed hands out; too big and the story stops teaching one
the seed never hands out (you'd never get it), unless that ability comes after the seed's last chapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# what each game-data override takes out of the story (so that only Archipelago hands it out); one at a time
OVERRIDE_REMOVES: dict[str, frozenset[str]] = {
    "flight-grant-removed": frozenset({"Flight"}),
    "story-grants-removed": frozenset({"Flight", "Double Jump"}),
    "story-abilities-removed": frozenset({"Flight", "Double Jump", "Focused Force Palm", "Back Hang",
                                          "Aerial Maneuver", "Call Dragon"}),
}
# items a story override exists for -> the overrides that take it out of the story
REQUIRED_OVERRIDES = {name: tuple(p for p, removes in OVERRIDE_REMOVES.items() if name in removes)
                      for name in sorted(set().union(*OVERRIDE_REMOVES.values()))}


def story_items_of(slot_data: dict, slice_no: int) -> list[str]:
    """The story abilities a seed hands out instead of the story (slot data story_items; older seeds: Flight, and
    Double Jump when the seed has draft slices past the first)."""
    if "story_items" in slot_data:
        return list(slot_data["story_items"])
    return ["Flight"] + (["Double Jump"] if slot_data.get("include_draft_slices") and slice_no >= 2 else [])


def best_override(needed: frozenset[str]) -> str | None:
    """The override that takes exactly these out of the story, else the smallest one that covers them."""
    if not needed:
        return None
    covering = sorted((p for p, removes in OVERRIDE_REMOVES.items() if needed <= removes),
                      key=lambda p: len(OVERRIDE_REMOVES[p]))
    exact = [p for p in covering if OVERRIDE_REMOVES[p] == needed]
    return (exact or covering or [None])[0]


def _plural(names: list[str]) -> tuple[str, str]:
    return ("them", "") if len(names) > 1 else ("it", "s")


@dataclass
class OverrideAdvice:
    """The seed's override against the game folder's."""
    needed: frozenset[str]
    installed: list[str] = field(default_factory=list)
    best: str | None = None
    missing: list[str] = field(default_factory=list)   # the story still teaches these, the seed hands them out
    extra: list[str] = field(default_factory=list)     # the story no longer teaches these, the seed doesn't either
    game_dir: Path | None = None
    no_game_dir: bool = False

    @property
    def ok(self) -> bool:
        return not (self.missing or self.extra or (self.no_game_dir and self.needed))

    @property
    def action(self) -> str:
        """What switching does, in a few words (a button's label)."""
        if self.best is None:
            return "Remove the override"
        return f"Install {self.best}" if not self.installed else f"Switch to {self.best}"

    @property
    def steps(self) -> str:
        if self.best is None:
            return "crimson-mod uninstall"
        return (f"crimson-mod uninstall, then crimson-mod install {self.best}" if self.installed
                else f"crimson-mod install {self.best}")

    def message(self) -> str | None:
        if self.ok:
            return None
        if self.no_game_dir:
            return ("The Crimson Desert game folder was not found (set CRIMSON_DESERT_DIR), so the client cannot "
                    "check that the Crimsonipelago game-data override is installed.")
        if self.missing:
            return (f"The story still hands out {', '.join(self.missing)}: the game-data override {self.best!r} isn't "
                    f"installed. With the game closed, run: {self.steps}")
        them, _ = _plural(self.extra)
        return (f"The installed game-data override takes {', '.join(self.extra)} out of the story, but this seed "
                f"doesn't hand {them} out: you'd never get {them}. With the game closed, run: {self.steps}")


def advise(catalogue, story_items, slice_no: int = 99, game_dir: str | Path | None = None) -> OverrideAdvice:
    from ..game_data import overlay
    from ..tools.game_data_cli import find_game_dir

    needed = frozenset(n for n in story_items if n in REQUIRED_OVERRIDES)
    advice = OverrideAdvice(needed=needed, best=best_override(needed))
    try:
        advice.game_dir = find_game_dir(str(game_dir) if game_dir else None)
    except SystemExit:
        advice.no_game_dir = True
        return advice
    advice.installed = sorted(ov.patch for ov in overlay.installed(advice.game_dir))
    removed = frozenset().union(*(OVERRIDE_REMOVES.get(p, frozenset()) for p in advice.installed))
    advice.missing = sorted(needed - removed)
    # taking an ability out of the story after the seed's last chapter doesn't matter
    advice.extra = sorted(n for n in removed - needed
                          if n in catalogue.items and catalogue.items[n].slice <= slice_no)
    return advice


def switch(advice: OverrideAdvice, *, force: bool = False) -> list[str]:
    """Make the game folder match the advice: remove what's installed, install the right override. The game must be
    closed (overlay refuses otherwise). Returns what was done, for the log."""
    from ..game_data import overlay
    from ..game_data.archive import GameArchive

    if advice.game_dir is None:
        raise overlay.OverlayError("the Crimson Desert game folder wasn't found")
    done = []
    removed = overlay.uninstall(advice.game_dir, force=force)
    if removed:
        done.append(f"removed {', '.join(advice.installed) or 'the old override'}")
    if advice.best is not None:
        built = overlay.build(GameArchive(advice.game_dir), overlay.PatchSpec.load(advice.best))
        result = overlay.install(advice.game_dir, built, force=force)
        done.append(f"installed {result.patch}")
    return done
