"""Developer tool: add the optional check categories as drafts: regional quests and Abyss ruins.

    python -m crimsonipelago.tools.draft_extras GAMEDATA research/candidates/regional_quests.json

GAMEDATA is a folder with `missioninfo` extracted. Each check only ever holds filler (`no_progression`): when a region
opens up is a guess from the story (REGION_SLICE), and a wrong guess must never block a seed. The categories are off
unless a player asks for them.

- regional: every objective of the regional quests (crimson-candidates' regional_quests.json), in a region per game
  region ("Regional quests: Hernand"), reachable from the chapter that brings the story there.
- abyss_ruins: one check per ruin (`Challenge_AbyssRuins_<code>_NNNN`, one objective each), in "Abyss ruins:
  Hernand" and so on. The Abyss island ruins need Flight.
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

# when the story first takes Kliff to each region (its slice), and the region's name
REGION_SLICE = {"Her": (2, "Hernand"), "Cal": (6, "Calphade"), "Kwe": (7, "Pailune and Beighen"),
                "Dem": (8, "Demeniss"), "Crim": (9, "the Crimson Desert"), "CD": (9, "the Crimson Desert"),
                "Crimson": (9, "the Crimson Desert"), "Wayward": (9, "the Wayward Woods"), "Del": (10, "Delesyia"),
                "AbyssIsland": (2, "the Abyss islands")}
# regional quests the candidates list without a region code, by their internal name
NAMED = {"Quest_Faction_LandSpider_Swamp_Boss": "Her", "Quest_Graymane_BaseCamp_Pailune": "Kwe",
         "Quest_GreyWolf_Camp_PailuneOperation": "Kwe", "Quest_Silver_Armor_Her_Beacon": "Cal"}
EVIDENCE = ("docs/research/drafts.md (optional category, draft from the game data; filler only, because when the "
            "region opens is a guess)")


def _entry_region(regions: list[dict], slice_no: int) -> str:
    """The first story region of a slice: where a region of the map opens up."""
    return next(r["name"] for r in regions if r.get("slice", 1) == slice_no and not r["name"].startswith(
        ("Regional quests", "Abyss ruins")))


def add(gamedata: Path, regional: Path, data: Path = DATA) -> dict:
    tables = load_tables(gamedata)
    nodes = story_mod.parse(tables["missioninfo"])
    docs = {n: json.loads((data / f"{n}.json").read_text(encoding="utf-8")) for n in ("locations", "regions")}
    locs, regs = docs["locations"], docs["regions"]
    taken = {loc["name"] for loc in locs["locations"]} | {loc["ap_id"] for loc in locs["locations"]}
    counts = {"regional": 0, "abyss_ruins": 0}

    def region(kind: str, code: str) -> str:
        slice_no, label = REGION_SLICE[code]
        name = f"{kind}: {label}"
        if name not in {r["name"] for r in regs["regions"]}:
            regs["regions"].append({"name": name, "slice": slice_no,
                                    "description": f"Optional ({kind.lower()}), from chapter {slice_no} (draft)."})
            source = _entry_region(regs["regions"], slice_no)
            requires = {"item": "Flight"} if code == "AbyssIsland" else None
            regs["connections"].append({"from": source, "to": name, "requires": requires})
        return name

    def check(name: str, where: str, category: str, key: int, parent: int) -> None:
        ap_id = ID_BASE + key
        if name in taken or ap_id in taken:
            return
        locs["locations"].append({
            "ap_id": ap_id, "name": name, "region": where, "category": category,
            "detection": {"kind": "mission_state", "mission_key": key, "states": [5], "parent_mission_key": parent},
            "requires": None, "missable": False, "repeatable_risk": False, "verified": False, "no_progression": True,
            "evidence": EVIDENCE, "caveats": None})
        taken.update({name, ap_id})
        counts[category] += 1

    for quest in json.loads(regional.read_text(encoding="utf-8"))["regional_quests"]:
        code = quest.get("region_code") or NAMED.get(quest.get("internal") or "")
        if code not in REGION_SLICE or not quest.get("missions"):
            continue
        where = region("Regional quests", code)
        for m in quest["missions"]:
            node = nodes.get(int(m["mission_key"]))
            if node is None or not m.get("display"):
                continue
            for n, child in enumerate(node.children, 1):
                if child in nodes:
                    check(f"{quest['display']}: {m['display']}" + (f" (step {n})" if len(node.children) > 1 else ""),
                          where, "regional", child, node.key)

    for node in sorted(nodes.values(), key=lambda n: n.internal):
        m = re.fullmatch(r"Challenge_AbyssRuins_([A-Za-z]+)_(\d+)", node.internal)
        if not m or m.group(1) not in REGION_SLICE or len(node.children) != 1 or node.children[0] not in nodes:
            continue
        where = region("Abyss ruins", m.group(1))
        check(f"Abyss Ruin: {REGION_SLICE[m.group(1)][1]} {int(m.group(2)):03d}", where, "abyss_ruins",
              node.children[0], node.key)

    for name, doc in docs.items():
        (data / f"{name}.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("gamedata", type=Path)
    parser.add_argument("regional", type=Path, help="regional_quests.json from crimson-candidates")
    parser.add_argument("--data", type=Path, default=DATA)
    args = parser.parse_args(argv)
    counts = add(args.gamedata, args.regional, args.data)
    print(f"added {counts['regional']} regional quest checks, {counts['abyss_ruins']} Abyss ruins")
    return 0


if __name__ == "__main__":
    sys.exit(main())
