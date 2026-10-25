"""Developer tool: add chapters to the APWorld catalogue as draft slices, from the story tree.

    crimson-data story GAMEDATA --plan research/candidates/main_story.json --json story.json
    python -m crimsonipelago.tools.draft_slices story.json --chapters 3-13 [--names mission_names.json]

Each chapter becomes one slice: a region per quest (in story order, each needing the one before), a check per
objective (detected by its mission state, or its mission's completion), and a goal at the chapter's last mission.
Everything is unverified: it's offered only with `include_draft_slices`, and the client reports it from the save
like any other check. The playthrough confirms it.

Story abilities later than Double Jump stay with the story for now (no override yet), so later chapters add no new
item gates: they need Double Jump through the chain from chapter 2.

Chapter indices are positions in story.json: 0 prologue, 1 chapter 1 … 12 chapter 12, 13 epilogue. The slice
number is the index (the first slice is prologue + chapter 1, so chapter N is slice N; the epilogue is 13).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "apworld" / "crimson_desert" / "data"
ID_BASE = 10_000_000_000      # location ids: 10,000,000,000 + the game's key (kind 1: mission/objective)
EVIDENCE = "docs/research/drafts.md (draft from the game data: missioninfo via crimson-data story; not yet seen in a save)"


def _names(path: Path | None) -> dict[int, str]:
    """Objective display names from the save editor's mission_names.json (key -> display), if given."""
    if path is None:
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw.get("missions", raw) if isinstance(raw, dict) else raw
    return {int(r["key"]): r.get("display") for r in rows if isinstance(r, dict) and r.get("display")}


def goal_key(index: int, total: int) -> str:
    return "journeys_end" if index == total - 1 else f"chapter_{index}"


def draft(story: list[dict], indices: list[int], data: Path = DATA, names: dict[int, str] | None = None) -> dict:
    names = names or {}
    docs = {n: json.loads((data / f"{n}.json").read_text(encoding="utf-8"))
            for n in ("locations", "regions", "progression")}
    locs, regs, prog = docs["locations"], docs["regions"], docs["progression"]
    taken_locs = {loc["name"] for loc in locs["locations"]}
    taken_ids = {loc["ap_id"] for loc in locs["locations"]}
    region_names = [r["name"] for r in regs["regions"]]
    seen_missions = {loc["name"].split(":")[0] for loc in locs["locations"]}
    added = {"locations": 0, "regions": 0, "goals": []}

    for index in indices:
        chapter = story[index]
        short = chapter["chapter"].split(":")[0]                       # "Chapter 3", "Epilogue"
        previous = region_names[-1]
        first_region = None
        last = None                                                     # (mission, its last objective's name)
        for quest in chapter["quests"]:
            region = quest["display"] if quest["display"] not in region_names else f"{quest['display']} ({short})"
            missions = [m for m in quest["missions"] if not m.get("missing") and m.get("objectives")]
            if not missions:
                continue
            regs["regions"].append({"name": region, "slice": index,
                                    "description": f"{chapter['chapter']}: {quest['display']} (draft)."})
            regs["connections"].append({"from": previous, "to": region, "requires": None})
            region_names.append(region)
            first_region = first_region or region
            previous = region
            added["regions"] += 1
            for m in missions:
                mission = m["display"] if m["display"] not in seen_missions else f"{m['display']} ({short})"
                seen_missions.add(m["display"])
                for n, o in enumerate(m["objectives"], 1):
                    name = f"{mission}: {names.get(o['key']) or f'step {n}'}"
                    if name in taken_locs:
                        name = f"{name} ({n})"
                    ap_id = ID_BASE + o["key"]
                    if ap_id in taken_ids:
                        continue
                    locs["locations"].append({
                        "ap_id": ap_id, "name": name, "region": region, "category": "quest",
                        "detection": {"kind": "mission_state", "mission_key": o["key"], "states": [5],
                                      "parent_mission_key": m["mission_key"]},
                        "requires": None, "missable": False, "repeatable_risk": False, "verified": False,
                        "evidence": EVIDENCE, "caveats": None})
                    taken_locs.add(name)
                    taken_ids.add(ap_id)
                    added["locations"] += 1
                    last = (m, name, region)
        if last is None:
            continue
        mission, objective, region = last
        key = goal_key(index, len(story))
        prog["goals"][key] = {
            "display": f"{mission['display']} (end of {short.lower() if short == 'Epilogue' else short})",
            "region": region, "slice": index, "requires": {"location": objective},
            "detection": {"kind": "mission_state", "mission_key": mission["mission_key"], "states": [5]},
            "verified": False}
        added["goals"].append(key)

    for name, doc in docs.items():
        (data / f"{name}.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return added


def _range(text: str) -> list[int]:
    out: list[int] = []
    for part in text.split(","):
        lo, _, hi = part.partition("-")
        out += list(range(int(lo), int(hi or lo) + 1))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("story", type=Path, help="story.json from crimson-data story --plan … --json")
    parser.add_argument("--chapters", required=True, help="story.json positions, e.g. 3-13")
    parser.add_argument("--names", type=Path, help="mission_names.json from the save editor (objective names)")
    parser.add_argument("--data", type=Path, default=DATA)
    args = parser.parse_args(argv)
    story = json.loads(args.story.read_text(encoding="utf-8"))
    added = draft(story, _range(args.chapters), args.data, _names(args.names))
    print(f"added {added['regions']} regions, {added['locations']} checks, goals {', '.join(added['goals'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
