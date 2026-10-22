"""crimson-mod: install or remove a Crimsonipelago game-data override.

    crimson-mod list                     bundled patches
    crimson-mod check PATCH              build it against your game without installing (shows what changes)
    crimson-mod install PATCH            install as an extra archive group, loaded before the game's own
    crimson-mod status                   what is installed
    crimson-mod uninstall                remove every Crimsonipelago override

The game's own archive groups are never modified. Install and uninstall only add/remove one group
folder and its entry in meta/0.papgt, and a timestamped copy of 0.papgt is kept before each change.
Steam's "Verify integrity of game files" restores the original state as well.
"""

from __future__ import annotations

import argparse
import sys

from ..game_data import overlay as ov
from ..game_data.archive import ArchiveError, GameArchive
from ..game_data.tables import TableError
from .game_data_cli import find_game_dir


def cmd_list(args) -> int:
    for path in sorted(ov.BUNDLED.glob("*.json")):
        spec = ov.PatchSpec.load(path)
        build = f" (made for build {spec.game_build})" if spec.game_build else ""
        print(f"{spec.name}{build}\n    {spec.description}")
    return 0


def _build(args) -> tuple[GameArchive, ov.BuiltOverlay]:
    game = GameArchive(find_game_dir(args.game))
    spec = ov.PatchSpec.load(args.patch)
    built = ov.build(game, spec)
    for line in built.log:
        print(f"  {line}")
    for path in sorted(built.files):
        print(f"  file {path} (from group {built.sources[path]})")
    return game, built


def cmd_check(args) -> int:
    _build(args)
    print("OK: the patch applies cleanly to this game version. Nothing was installed.")
    return 0


def cmd_install(args) -> int:
    game, built = _build(args)
    result = ov.install(game.root, built, force=args.force)
    print(f"installed {result.patch!r} as group {result.name} in {game.root}")
    print("Uninstall any time with: crimson-mod uninstall")
    return 0


def cmd_status(args) -> int:
    root = find_game_dir(args.game)
    items = ov.installed(root)
    if not items:
        print(f"no Crimsonipelago override installed in {root}")
        return 0
    first = GameArchive(root).groups[0].name
    for it in items:
        state = "active (loads first)" if it.name == first else "registered but NOT first in the load order"
        print(f"group {it.name}: {it.patch}, installed {it.installed_at}, {state}")
    return 0


def cmd_uninstall(args) -> int:
    root = find_game_dir(args.game)
    removed = ov.uninstall(root, force=args.force)
    print(f"removed group(s) {', '.join(removed)}" if removed else "nothing to remove")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="crimson-mod", description="Install or remove Crimsonipelago game-data overrides",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--game", help="game folder; found via Steam by default")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("list").set_defaults(func=cmd_list)
    for name, func, help_text in (("check", cmd_check, "dry run against your game"),
                                  ("install", cmd_install, "install a patch")):
        c = sub.add_parser(name, help=help_text)
        c.add_argument("patch", help="bundled patch name (see `list`) or a .json file")
        if name == "install":
            c.add_argument("--force", action="store_true", help="proceed when the game-running check is unavailable")
        c.set_defaults(func=func)
    sub.add_parser("status").set_defaults(func=cmd_status)
    u = sub.add_parser("uninstall")
    u.add_argument("--force", action="store_true", help="proceed when the game-running check is unavailable")
    u.set_defaults(func=cmd_uninstall)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ov.OverlayError, ArchiveError, TableError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
