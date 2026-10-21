"""crimson-candidates: draft catalogue candidates from the NattKh save editor's game-data tables.

Everything this produces is UNVERIFIED. It is a shopping list for the save-diff
discovery workflow (docs/reverse_engineering.md), never catalogue data by itself.

The editor's data files are MPL-2.0 and are read from the user's own checkout;
the generated lists are written locally (research/candidates/, git-ignored) and
are not redistributed with Crimsonipelago.

Inputs (from <CrimsonSaveEditor>/):
    quest_database.json    quests: key, internal name, display, category, quest_group, missions, stages
    mission_names.json     mission key -> internal name, display
    knowledge_keys_all.json knowledge key -> internal name, display (abilities, codex, discoveries, ...)
    item_names.json        item key -> name, category, internal name, max stack
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Main-story quest groups as they appear in quest_database.json. The mapping to chapters was
# cross-checked against published chapter lists (see docs/research/progression-draft.md).
MAIN_STORY_GROUPS: dict[int, tuple[str, str]] = {
    1000: ("Prologue", "Dead of Night"),
    1001: ("Chapter 1", "The First Encounter"),
    1002: ("Chapter 2", "Golden Greed"),
    1003: ("Chapter 3", "Howling Hill"),
    1004: ("Chapter 4", "The Price of Knowledge"),
    1005: ("Chapter 5", "Guest Unbidden"),
    1006: ("Chapter 6", "Cracks in the Shield"),
    1007: ("Chapter 7", "Homecoming"),
    1008: ("Chapter 8", "Blood Coronation"),
    1009: ("Chapter 9", "The Sage of the Desert"),
    1010: ("Chapter 10", "Counterattack"),
    1011: ("Chapter 11", "Truth and Reality"),
    1012: ("Chapter 12", "The Abyss"),
    1013: ("Epilogue", "Journey's End"),
}

# Some main quests carry a character name as their display text instead of the quest title.
CHARACTER_NAMES = {"Damiane", "Yann", "Kliff", "Oongka", "Naira", "Ludvig", "Myurdin"}
# Their real titles, identified by matching their mission names against published main-quest lists.
QUEST_TITLE_OVERRIDES: dict[int, str] = {
    10011000: "Hernand in Chaos",
    1000118: "Uninvited Guest",
    1000724: "Black and White",
    1000579: "Dawn Mist",
    1000725: "To Demeniss",
    1000354: "The Calling",
    1000767: "Secret Weapon",
}

# Movement / exploration abilities, by internal-name stem. A character prefix (Damian_, Oongka_)
# is allowed in front of every stem; no prefix means Kliff.
TRAVERSAL_STEMS = (
    "CrowWing", "CrowWing_MoveFast", "CrowDash", "DoubleJump", "DoubleJumpStep", "JumpStep",
    "AirJump", "ShieldGlider", "ShieldGlider_MoveFast", "AirGrab",
    "Rope_Swing", "Rope_AirPull", "Rope_AirSwing",
    "JiJeongTa_I", "JiJeongTa_Air", "JiJeongTa_Deep", "JiJeongTa_Far", "JiJeongTa_Long_Distance",
    "CharacterClimb", "CallDragon",
    # confirmed in real saves (2026-10-21, build 25477059); internal names that can't be guessed from display text
    "BlackWolf",          # Axiom Force (grapple), granted during "Polar Opposites"
    "Light_Reflection",   # Blinding Flash, granted in the Prologue
    "CallVehicle",        # Summon Mount
)
CHARACTER_PREFIXES = {"Damian": "Damiane", "Oongka": "Oongka"}
OTHER_ABILITY_GROUPS = {
    "elemental": re.compile(r"^Knowledge_(?:(?:Damian|Oongka)_)?ElementalReinforce"),
    "blackstar_dragon": re.compile(r"^Knowledge_Skill_TriStar_"),
    "mount": re.compile(r"^Knowledge_S[Kk]ill_(?:Horse|Riding)"),
    "life_skill": re.compile(r"^Knowledge_Skill_(?!Horse|Riding|TriStar|WarMachine)"),
}

ITEM_GROUPS: dict[str, re.Pattern[str]] = {
    "abyss_artifact": re.compile(r"^(?:First_Artifact|Abyss_Artifact|Deactived_Abyss_Artifact|Sealed_Abyss_Artifact_\d+)$"),
    "abyss_stat_upgrade": re.compile(r"^Abyss_InfiniteStat_"),
    "abyss_unique_gear": re.compile(r"^AbyssReward_"),
    "key": re.compile(r"(?:^|_)Key(?:_|$)|_key$", re.IGNORECASE),
    "entry_permit": re.compile(r"^(?:Permit_\w+_Pass|MonasteryVisitorPass)$"),
    "supply_contract": re.compile(r"^Permit_\w+_(?:Seed|Costume|Equipment|General|Tavern|Furniture)$"),
    "treasure_map": re.compile(r"^TreasureMap_"),
    "blueprint": re.compile(r"Blueprint|^Recipe_|^CraftingRecipe_", re.IGNORECASE),
}


@dataclass
class EditorData:
    quests: list[dict[str, Any]]
    missions: dict[int, dict[str, Any]]
    knowledge: list[dict[str, Any]]
    items: list[dict[str, Any]]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_editor_data(editor_dir: Path) -> EditorData:
    missing = [n for n in ("quest_database.json", "mission_names.json", "knowledge_keys_all.json", "item_names.json")
               if not (editor_dir / n).is_file()]
    if missing:
        raise SystemExit(f"{editor_dir} is not a CrimsonSaveEditor folder (missing {', '.join(missing)})")
    raw_items = _load_json(editor_dir / "item_names.json")
    items = raw_items.get("items", raw_items) if isinstance(raw_items, dict) else raw_items
    if isinstance(items, str):  # the editor stores this list as a Python-literal string
        items = ast.literal_eval(items)
    return EditorData(
        quests=_load_json(editor_dir / "quest_database.json"),
        missions={int(m["key"]): m for m in _load_json(editor_dir / "mission_names.json")},
        knowledge=_load_json(editor_dir / "knowledge_keys_all.json"),
        items=list(items),
    )


def _display(entry: dict[str, Any]) -> str | None:
    return entry.get("display_name") or entry.get("display") or None


# --------------------------------------------------------------------------- quests
def main_story(data: EditorData) -> list[dict[str, Any]]:
    chapters: dict[int, dict[str, Any]] = {}
    for q in data.quests:
        group = int(q.get("quest_group", -1))
        if group not in MAIN_STORY_GROUPS or q.get("category_name") != "Main Story":
            continue
        label, title = MAIN_STORY_GROUPS[group]
        chapter = chapters.setdefault(group, {"quest_group": group, "chapter": label, "title": title, "quests": []})
        display = q.get("display") or q.get("name")
        key = int(q["key"])
        chapter["quests"].append({
            "quest_key": key,
            "internal": q.get("name"),
            "display": QUEST_TITLE_OVERRIDES.get(key, display),
            "data_display": display,
            "display_is_character_name": display in CHARACTER_NAMES and key not in QUEST_TITLE_OVERRIDES,
            "missions": [_mission(data, k) for k in q.get("missions", [])],
            "stage_count": len(q.get("stages", [])),
        })
    return [chapters[g] for g in sorted(chapters)]


def _mission(data: EditorData, key: int) -> dict[str, Any]:
    m = data.missions.get(int(key), {})
    return {"mission_key": int(key), "internal": m.get("name"), "display": m.get("display")}


def regional_quests(data: EditorData) -> list[dict[str, Any]]:
    out = []
    for q in data.quests:
        if q.get("category_name") != "Regional":
            continue
        out.append({"quest_key": int(q["key"]), "internal": q.get("name"), "display": q.get("display"),
                    "region_code": _region_code(q.get("name", "")),
                    "missions": [_mission(data, k) for k in q.get("missions", [])]})
    return sorted(out, key=lambda x: (x["region_code"] or "~", x["internal"] or ""))


def _region_code(internal: str) -> str | None:
    m = re.match(r"Quest_Node_([A-Za-z]+)_", internal)
    return m.group(1) if m else None


def side_quest_summary(data: EditorData) -> dict[str, Any]:
    side = [q for q in data.quests if q.get("category_name") == "Side"]
    internal_noise = [q for q in side if re.match(r"^(?:Func_|LevelSequencer|Test|Debug)", q.get("name", ""))
                      or "_Test" in q.get("name", "")]
    return {"total": len(side), "looks_internal": len(internal_noise),
            "with_missions": sum(1 for q in side if q.get("missions"))}


# --------------------------------------------------------------------------- abilities
def abilities(data: EditorData) -> dict[str, list[dict[str, Any]]]:
    stem_re = re.compile(r"^Knowledge_(?:(Damian|Oongka)_)?(" + "|".join(sorted(map(re.escape, TRAVERSAL_STEMS),
                                                                             key=len, reverse=True)) + r")$")
    groups: dict[str, list[dict[str, Any]]] = {"traversal": [], **{g: [] for g in OTHER_ABILITY_GROUPS}}
    for k in data.knowledge:
        name = k.get("name", "")
        entry = {"knowledge_key": int(k["key"]), "internal": name, "display": _display(k)}
        m = stem_re.match(name)
        if m:
            entry["character"] = CHARACTER_PREFIXES.get(m.group(1) or "", "Kliff")
            groups["traversal"].append(entry)
            continue
        for group, pat in OTHER_ABILITY_GROUPS.items():
            if pat.match(name):
                groups[group].append(entry)
                break
    for entries in groups.values():
        entries.sort(key=lambda e: (e.get("character", ""), e["internal"]))
    return groups


def discovery_summary(data: EditorData) -> dict[str, Any]:
    nodes = Counter()
    other = Counter()
    for k in data.knowledge:
        name = k.get("name", "")
        m = re.match(r"^Knowledge_Node_([A-Za-z]+)_", name)
        if m:
            nodes[m.group(1)] += 1
        elif name.startswith("AbyssGate_"):
            other["abyss_gate"] += 1
        elif name.startswith("Knowledge_AbyssRuins"):
            other["abyss_ruins"] += 1
        elif name.startswith("Knowledge_Discover_"):
            other["landmark"] += 1
    return {"map_nodes_by_region_code": dict(nodes.most_common()), "other": dict(other)}


# --------------------------------------------------------------------------- items
def item_groups(data: EditorData) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {g: [] for g in ITEM_GROUPS}
    groups["quest_item"] = []
    for it in data.items:
        internal = it.get("internalName", "")
        entry = {"item_key": int(it["itemKey"]), "name": it.get("name"), "internal": internal,
                 "category": it.get("category"), "max_stack": it.get("maxStack")}
        for group, pat in ITEM_GROUPS.items():
            if pat.search(internal):
                groups[group].append(entry)
                break
        else:
            if it.get("category") == "Quest":
                groups["quest_item"].append(entry)
    for entries in groups.values():
        entries.sort(key=lambda e: e["item_key"])
    return groups


# --------------------------------------------------------------------------- output
def build(editor_dir: Path) -> dict[str, Any]:
    data = load_editor_data(editor_dir)
    return {
        "main_story": main_story(data),
        "regional_quests": regional_quests(data),
        "side_quests": side_quest_summary(data),
        "abilities": abilities(data),
        "discoveries": discovery_summary(data),
        "items": item_groups(data),
    }


def write(result: dict[str, Any], out: Path, source: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    header = {"_status": "UNVERIFIED candidates generated from the NattKh save editor's data tables. "
                         "Confirm every entry with the save-diff workflow before it enters the catalogue.",
              "_source": str(source)}
    written = []
    for name, payload in result.items():
        path = out / f"{name}.json"
        path.write_text(json.dumps({**header, name: payload}, indent=2, ensure_ascii=False), encoding="utf-8")
        written.append(path)
    return written


def summary(result: dict[str, Any]) -> str:
    lines = ["Candidate summary (unverified)", ""]
    chapters = result["main_story"]
    quests = sum(len(c["quests"]) for c in chapters)
    missions = sum(len(q["missions"]) for c in chapters for q in c["quests"])
    lines.append(f"main story: {len(chapters)} chapters, {quests} quests, {missions} missions")
    for c in chapters:
        names = ", ".join(q["display"] + ("*" if q["display_is_character_name"] else "") for q in c["quests"])
        lines.append(f"  {c['chapter']:<11} {c['title']:<24} {names}")
    if any(q["display_is_character_name"] for c in chapters for q in c["quests"]):
        lines.append("  (* display text is a character name; the missions identify the quest)")
    lines.append(f"regional quests: {len(result['regional_quests'])}")
    sq = result["side_quests"]
    lines.append(f"side quests: {sq['total']} ({sq['looks_internal']} look internal, {sq['with_missions']} have missions)")
    ab = result["abilities"]
    lines.append("abilities: " + ", ".join(f"{g} {len(v)}" for g, v in ab.items()))
    kliff = [a for a in ab["traversal"] if a["character"] == "Kliff"]
    lines.append("  Kliff traversal: " + ", ".join(f"{a['display']} ({a['knowledge_key']})" for a in kliff))
    disc = result["discoveries"]
    lines.append(f"discoveries: map nodes {sum(disc['map_nodes_by_region_code'].values())} "
                 f"({len(disc['map_nodes_by_region_code'])} region codes), other {disc['other']}")
    lines.append("items: " + ", ".join(f"{g} {len(v)}" for g, v in result["items"].items()))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="crimson-candidates", description=__doc__.splitlines()[0])
    p.add_argument("--editor-dir", type=Path, required=True, help="your CrimsonSaveEditor folder")
    p.add_argument("-o", "--out", type=Path, default=Path("research") / "candidates")
    p.add_argument("--quiet", action="store_true", help="do not print the summary")
    args = p.parse_args(argv)
    result = build(args.editor_dir)
    files = write(result, args.out, args.editor_dir)
    if not args.quiet:
        print(summary(result))
        print()
    print(f"wrote {len(files)} files to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
