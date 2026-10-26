"""Developer tool: add the game's challenge missions as optional draft check categories.

    python -m crimsonipelago.tools.draft_challenges GAMEDATA CrimsonSaveEditor/mission_names.json

GAMEDATA is a folder with `missioninfo` extracted. One check per challenge, detected by the challenge mission's own
completion (mission state 5), named from the save editor's mission names:

| Category | Missions | Region (slice) |
|---|---|---|
| abyss_challenges | `Challenge_Abyss_NNNN`, `Challenge_AbyssIslandCore` | "Abyss challenges" (2, needs Flight) |
| standstone | `Challenge_Standstone_Adventure_*`, `Challenge_Standstone_Puzzle_*` | "Standstone ruins" (whole game) |
| sealed_artifacts | `Challenge_SealedArtifact_*` (trials that open a Sealed Abyss Artifact) | "Sealed Abyss Artifacts" (13) |
| spires | `Challenge_Discover_*` (the spires into the Abyss) | "Abyss spires" (whole game) |

Where on the map each one is isn't in the data. So the last three only join whole-game seeds (slice 13), where the
whole map is open, and every check here only ever holds filler (`no_progression`), like the regional quests and
ruins. Challenges already in the catalogue (by mission key) are skipped. Only missions that have objectives count:
their objectives are steps of the same challenge.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from ..game_data import story as story_mod
from ..game_data.tables import load_tables
from .draft_slices import DATA, ID_BASE
from .name_checks import clean
from .record_fingerprints import record

FAMILIES = [
    # category, internal name pattern, region, slice, label for names, requires
    ("abyss_challenges", r"Challenge_Abyss_\d+|Challenge_AbyssIslandCore", "Abyss challenges", 2, "Abyss",
     {"item": "Flight"}),
    ("standstone", r"Challenge_Standstone_(Adventure|Puzzle)_.*", "Standstone ruins", 13, "Standstone", None),
    ("sealed_artifacts", r"Challenge_SealedArtifact_.*", "Sealed Abyss Artifacts", 13, "Sealed Artifact", None),
    ("spires", r"Challenge_Discover_.*", "Abyss spires", 13, "Spire", None),
]
ENTRY = {2: "Traces", 13: "Prologue"}     # where each region hangs off: before the jump, or the start
EVIDENCE = ("docs/research/drafts.md (optional category, draft from the game data; filler only, because where and "
            "when each challenge opens isn't in the data)")


def add(gamedata: Path, names_file: Path, data: Path = DATA) -> dict[str, int]:
    nodes = story_mod.parse(load_tables(gamedata)["missioninfo"])
    raw = json.loads(names_file.read_text(encoding="utf-8"))
    rows = raw.get("missions", raw) if isinstance(raw, dict) else raw
    names = {int(r["key"]): clean(r["display"]) for r in rows if isinstance(r, dict) and r.get("display")}
    docs = {n: json.loads((data / f"{n}.json").read_text(encoding="utf-8")) for n in ("locations", "regions")}
    locs, regs = docs["locations"], docs["regions"]
    known = {loc["detection"].get(k) for loc in locs["locations"] for k in ("mission_key", "parent_mission_key")}
    taken_names = {loc["name"] for loc in locs["locations"]}
    taken_ids = {loc["ap_id"] for loc in locs["locations"]}
    counts = {family[0]: 0 for family in FAMILIES}

    for category, pattern, region, slice_no, label, requires in FAMILIES:
        found = sorted((n for n in nodes.values() if re.fullmatch(pattern, n.internal) and n.children
                        and n.key not in known), key=lambda n: n.internal)
        if not found:
            continue
        if region not in {r["name"] for r in regs["regions"]}:
            regs["regions"].append({"name": region, "slice": slice_no,
                                    "description": f"Optional ({category}), draft: the game's challenge missions."})
            regs["connections"].append({"from": ENTRY[slice_no], "to": region, "requires": requires})
        for node in found:
            title = names.get(node.key) or node.internal.removeprefix("Challenge_").replace("_", " ")
            name, n = f"{label}: {title}", 2
            while name in taken_names:
                name, n = f"{label}: {title} ({n})", n + 1
            ap_id = ID_BASE + node.key
            if ap_id in taken_ids:
                continue
            locs["locations"].append({
                "ap_id": ap_id, "name": name, "region": region, "category": category,
                "detection": {"kind": "mission_state", "mission_key": node.key, "states": [5]},
                "requires": None, "missable": False, "repeatable_risk": False, "verified": False,
                "no_progression": True, "evidence": EVIDENCE, "caveats": None})
            taken_names.add(name)
            taken_ids.add(ap_id)
            counts[category] += 1

    for name, doc in docs.items():
        (data / f"{name}.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    record(data)
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("gamedata", type=Path)
    parser.add_argument("names", type=Path, help="mission_names.json from the save editor")
    parser.add_argument("--data", type=Path, default=DATA)
    args = parser.parse_args(argv)
    counts = add(args.gamedata, args.names, args.data)
    print(", ".join(f"{k} {v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
