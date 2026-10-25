"""Developer tool: record the catalogue's seed fingerprints, so seeds generated now stay playable after updates.

    python -m crimsonipelago.tools.record_fingerprints

A seed carries its catalogue's scope fingerprint. A later catalogue (a draft check confirmed, a name filled in, a
rule relaxed, checks or items added) has a different one. This records, in data/seed_fingerprints.json:

- `seeds`: every goal's scope fingerprint -> [catalogue id, goal];
- `catalogues`: per catalogue id, what its seeds play by (Catalogue.play_entries: each check's detection and each
  item's delivery as a short hash, with its slice).

A client with a newer catalogue looks a seed's fingerprint up and accepts the seed when every check and item it was
made with still exists (Catalogue.compare_seed). Run this after every change to the catalogue data (a test fails
until it's recorded). Entries are never removed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from crimson_desert import catalogue as cat_mod

from .draft_slices import DATA

COMMENT = ("Seed fingerprints of every catalogue so far, and what seeds made from each play by. The client accepts "
           "a seed from an earlier catalogue when every check and item it was made with still exists. Written by "
           "crimsonipelago.tools.record_fingerprints; never remove entries.")


def catalogue_id(entries: dict) -> str:
    return hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]


def add(doc: dict, cat) -> list[str]:
    """Record this catalogue's fingerprints in doc; returns what was new."""
    entries = cat.play_entries()
    cat_id = catalogue_id(entries)
    doc.setdefault("catalogues", {})
    seeds = doc.setdefault("seeds", {})
    added = []
    for goal in sorted(cat.goals):
        scope = cat.scope_fingerprint(goal)
        if scope not in seeds:
            seeds[scope] = [cat_id, goal]
            doc["catalogues"].setdefault(cat_id, entries)
            added.append(f"{goal}: {scope} (catalogue {cat_id})")
    return added


def record(data: Path = DATA) -> list[str]:
    cat = cat_mod.load(data)
    path = data / f"{cat_mod.SEED_FINGERPRINTS}.json"
    doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"_comment": COMMENT}
    added = add(doc, cat)
    if added:
        path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
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
