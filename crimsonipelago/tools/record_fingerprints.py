"""Developer tool: record the catalogue's seed fingerprints, so seeds generated now stay playable after updates.

    python -m crimsonipelago.tools.record_fingerprints

A seed carries its catalogue's scope fingerprint. A later catalogue (a draft check confirmed, a name filled in, a
rule relaxed) has a different one, so the client would refuse the seed. This records, for every goal, the scope
fingerprint and the play fingerprint (what the client plays a seed by) in data/seed_fingerprints.json. A client
with a newer catalogue looks the seed's fingerprint up there and accepts the seed when the play fingerprint is
still the same.

Run it after every change to the catalogue data (a test fails until it's recorded). Entries are never removed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from crimson_desert import catalogue as cat_mod

from .draft_slices import DATA


def record(data: Path = DATA) -> list[str]:
    cat = cat_mod.load(data)
    path = data / f"{cat_mod.SEED_FINGERPRINTS}.json"
    doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "_comment": "Seed (scope) fingerprints of every catalogue so far, with the play fingerprint they had. The "
                    "client accepts a seed from an earlier catalogue when its play fingerprint is unchanged. "
                    "Written by crimsonipelago.tools.record_fingerprints; never remove entries.",
        "seeds": {}}
    added = []
    for goal in sorted(cat.goals):
        scope, play = cat.scope_fingerprint(goal), cat.play_fingerprint(goal)
        known = doc["seeds"].get(scope)
        if known is None:
            doc["seeds"][scope] = play
            added.append(f"{goal}: {scope} -> {play}")
        elif known != play:
            raise SystemExit(f"{goal}: {scope} was recorded with play fingerprint {known}, now {play}. The play "
                             f"fingerprint's definition changed: that would break running seeds.")
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return added


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", type=Path, default=DATA)
    args = parser.parse_args(argv)
    added = record(args.data)
    print("\n".join(added) if added else "all goals already recorded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
