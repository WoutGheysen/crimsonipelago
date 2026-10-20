"""crimson-catalogue: validate the game data catalogue and report its readiness.

    crimson-catalogue validate [--dir DIR]   internal consistency (exit 1 on problems)
    crimson-catalogue report   [--dir DIR]   how much of the catalogue is verified / deliverable
    crimson-catalogue ids --against REF_DIR  fail if any released ID changed or disappeared without retirement
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from crimson_desert import catalogue as cat_mod


def cmd_validate(args) -> int:
    cat = cat_mod.build(cat_mod.load_raw(args.dir))
    problems = cat_mod.validate_catalogue(cat)
    if problems:
        print("Catalogue problems:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"OK: {len(cat.items)} items, {len(cat.locations)} locations, {len(cat.regions)} regions, "
          f"status {cat.status}, fingerprint {cat.fingerprint}")
    return 0


def cmd_report(args) -> int:
    cat = cat_mod.load(args.dir)
    items, locs = list(cat.items.values()), list(cat.locations.values())
    by_cap: dict[str, int] = {}
    for i in items:
        by_cap[i.capability] = by_cap.get(i.capability, 0) + 1
    print(f"catalogue status: {cat.status}")
    print(f"items:     {len(items)} total, {sum(i.verified for i in items)} verified, "
          f"{sum(i.deliverable for i in items)} deliverable; by capability {by_cap}")
    print(f"locations: {len(locs)} total, {sum(loc.verified for loc in locs)} verified, "
          f"{sum(loc.detectable for loc in locs)} detectable from saves")
    goals = ", ".join(f"{g.key} ({'verified' if g.verified else 'unverified'})" for g in cat.goals.values())
    print(f"goals:     {goals}")
    if cat.is_placeholder:
        print("\nThis is the PLACEHOLDER catalogue: fine for development, not for a release.")
    return 0


def _ids(directory) -> tuple[dict[str, int], dict[str, int], dict]:
    raw = cat_mod.load_raw(directory)
    items = {e["name"]: int(e["ap_id"]) for e in raw["items"]["items"]}
    locs = {e["name"]: int(e["ap_id"]) for e in raw["locations"]["locations"]}
    return items, locs, raw["compatibility"]


def cmd_ids(args) -> int:
    """Released IDs are a contract with every generated seed: they must never change."""
    old_items, old_locs, _ = _ids(args.against)
    new_items, new_locs, compat = _ids(args.dir)
    problems = []
    for kind, old, new, retired in (("item", old_items, new_items, set(compat.get("retired_item_ids", []))),
                                    ("location", old_locs, new_locs, set(compat.get("retired_location_ids", [])))):
        new_by_id = {v: k for k, v in new.items()}
        for name, ap_id in old.items():
            if ap_id in retired:
                continue
            if ap_id not in new_by_id:
                problems.append(f"{kind} id {ap_id} ({name!r}) disappeared; add it to retired_{kind}_ids instead")
            elif new_by_id[ap_id] != name:
                problems.append(f"{kind} id {ap_id} was renamed {name!r} -> {new_by_id[ap_id]!r}")
            if name in new and new[name] != ap_id:
                problems.append(f"{kind} {name!r} changed id {ap_id} -> {new[name]}")
    for p in problems:
        print(f"  - {p}")
    print("IDs stable." if not problems else f"{len(problems)} ID problem(s).")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="crimson-catalogue")
    p.add_argument("--dir", type=Path, default=None, help="catalogue folder (default: the packaged one)")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("validate").set_defaults(func=cmd_validate)
    sub.add_parser("report").set_defaults(func=cmd_report)
    ids = sub.add_parser("ids")
    ids.add_argument("--against", type=Path, required=True, help="catalogue folder of the last release")
    ids.set_defaults(func=cmd_ids)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
