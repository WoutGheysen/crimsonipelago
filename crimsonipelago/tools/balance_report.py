"""Developer tool: where items land, from a set of generated seeds' spoiler logs (the balance pass).

    python Generate.py --player_files_path P --outputpath OUT/o1 --seed 1 --spoiler 2      # in Archipelago, N times
    python -m crimsonipelago.tools.balance_report OUT [--watch Flight "Double Jump" Vault]

For each watched item: how far into the story its check is (the check's position in the catalogue, which follows
the story) and which chapters (slices) it lands in. Then the average count of every item per seed, and per chapter.
Only Crimson Desert checks are counted; other games' items in them are counted by name like any other.
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

from crimson_desert.catalogue import default_catalogue


def placements(spoiler: str) -> list[tuple[str, str]]:
    """(location, item) pairs from a spoiler log's Locations section."""
    block = spoiler.split("\nLocations:\n", 1)[1].split("\n\nPlaythrough:", 1)[0]
    out = []
    for line in block.splitlines():
        if ": " in line:
            loc, item = line.rsplit(": ", 1)
            out.append((re.sub(r" \(.*\)$", "", loc), re.sub(r" \(.*\)$", "", item)))
    return out


def report(folder: Path, watch: list[str], cat=None) -> list[str]:
    cat = cat or default_catalogue()
    order = {loc.name: i for i, loc in enumerate(cat.locations.values())}
    slice_of = {loc.name: cat.region_slices.get(loc.region, 1) for loc in cat.locations.values()}
    spoilers = sorted(folder.rglob("*_Spoiler.txt"))
    pos, slices = defaultdict(list), defaultdict(list)
    totals: Counter = Counter()
    per_slice: dict[int, Counter] = defaultdict(Counter)
    for path in spoilers:
        for loc, item in placements(path.read_text(encoding="utf-8")):
            if loc not in order:
                continue
            totals[item] += 1
            per_slice[slice_of[loc]][item] += 1
            if item in watch:
                pos[item].append(order[loc])
                slices[item].append(slice_of[loc])
    n = len(spoilers) or 1
    out = [f"{len(spoilers)} seeds, {sum(totals.values()) / n:.0f} Crimson Desert checks each"]
    for item in watch:
        if pos[item]:
            out.append(f"{item}: check {statistics.median(pos[item]):.0f} of the story (median; {min(pos[item])}-"
                       f"{max(pos[item])}), chapters {dict(sorted(Counter(slices[item]).items()))}")
    out.append("per seed: " + ", ".join(f"{k} {v / n:.1f}" for k, v in totals.most_common()))
    for s in sorted(per_slice):
        c = per_slice[s]
        out.append(f"  chapter {s:>2}: {sum(c.values()) / n:4.0f} checks; "
                   + ", ".join(f"{k} {v / n:.1f}" for k, v in c.most_common(6)))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder", type=Path, help="folder with the seeds' *_Spoiler.txt (searched recursively)")
    parser.add_argument("--watch", nargs="*", default=["Flight", "Double Jump"])
    args = parser.parse_args(argv)
    print("\n".join(report(args.folder, args.watch)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
