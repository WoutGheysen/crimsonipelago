"""crimson-candidates on a tiny synthetic CrimsonSaveEditor folder."""

import json

from crimsonipelago.tools import candidates


def _editor(tmp_path):
    d = tmp_path / "CrimsonSaveEditor"
    d.mkdir()
    quests = [
        {"key": 10001, "name": "Quest_Intro", "display": "Ambush", "category_name": "Main Story",
         "quest_group": 1000, "missions": [1], "stages": [11, 12]},
        {"key": 10011000, "name": "Quest_SplitHorn_Boss_ThiefCave", "display": "Damiane", "category_name": "Main Story",
         "quest_group": 1002, "missions": [2], "stages": []},
        {"key": 1000123, "name": "Quest_Somebody", "display": "Yann", "category_name": "Main Story",
         "quest_group": 1009, "missions": [], "stages": []},
        {"key": 555, "name": "Quest_Node_Her_Village_Block", "display": "Village", "category_name": "Regional",
         "quest_group": 0, "missions": [], "stages": []},
        {"key": 777, "name": "Func_LevelSequencerSpawn", "display": "x", "category_name": "Side",
         "quest_group": 9999, "missions": [], "stages": []},
    ]
    missions = [{"key": 1, "name": "Mission_Intro", "display": "Unfamiliar Lands"},
                {"key": 2, "name": "Mission_Honor", "display": "For Honor"}]
    knowledge = [
        {"key": 40002, "name": "Knowledge_CrowWing", "display_name": "Flight"},
        {"key": 1001541, "name": "Knowledge_Damian_ShieldGlider", "display_name": "Flight"},
        {"key": 1004189, "name": "Knowledge_Skill_TriStar_Attack", "display_name": "Blackstar - Basic Attack"},
        {"key": 1004213, "name": "Knowledge_Skill_Farming_I", "display_name": "Novice Farmer"},
        {"key": 1006, "name": "Knowledge_Hedgehog", "display_name": "Hedgehog"},
        {"key": 9, "name": "Knowledge_Node_Her_Serkis", "display_name": "Serkis"},
        {"key": 25001, "name": "AbyssGate_0001", "display": "Dimension's End"},
    ]
    items = [
        {"itemKey": 75001, "name": "Abyss Artifact", "category": "Misc", "internalName": "Abyss_Artifact", "maxStack": 10},
        {"itemKey": 801001, "name": "Hernand Entry Permit", "category": "Misc", "internalName": "Permit_Her_Pass",
         "maxStack": 1},
        {"itemKey": 1003153, "name": "Equipment Supply Contract", "category": "Misc",
         "internalName": "Permit_Her_Equipment", "maxStack": 1},
        {"itemKey": 75100, "name": "Greymane's Token", "category": "Quest", "internalName": "Quest_GrayBrotherMark",
         "maxStack": 999},
        {"itemKey": 1, "name": "Copper", "category": "Currency", "internalName": "Money_Copper", "maxStack": 100000},
    ]
    (d / "quest_database.json").write_text(json.dumps(quests))
    (d / "mission_names.json").write_text(json.dumps(missions))
    (d / "knowledge_keys_all.json").write_text(json.dumps(knowledge))
    # the editor stores the item list as a Python-literal string
    (d / "item_names.json").write_text(json.dumps({"version": "2", "items": repr(items)}))
    return d


def test_build_classifies_everything(tmp_path):
    result = candidates.build(_editor(tmp_path))

    chapters = result["main_story"]
    assert [c["chapter"] for c in chapters] == ["Prologue", "Chapter 2", "Chapter 9"]
    intro = chapters[0]["quests"][0]
    assert intro["missions"] == [{"mission_key": 1, "internal": "Mission_Intro", "display": "Unfamiliar Lands"}]
    assert intro["stage_count"] == 2
    hernand = chapters[1]["quests"][0]
    assert hernand["display"] == "Hernand in Chaos" and hernand["data_display"] == "Damiane"
    assert not hernand["display_is_character_name"]
    assert chapters[2]["quests"][0]["display_is_character_name"]  # unknown character-named quest is flagged

    assert result["regional_quests"][0]["region_code"] == "Her"
    assert result["side_quests"] == {"total": 1, "looks_internal": 1, "with_missions": 0}

    ab = result["abilities"]
    assert {(a["knowledge_key"], a["character"]) for a in ab["traversal"]} == {(40002, "Kliff"), (1001541, "Damiane")}
    assert [a["knowledge_key"] for a in ab["blackstar_dragon"]] == [1004189]
    assert [a["knowledge_key"] for a in ab["life_skill"]] == [1004213]
    assert all(a["knowledge_key"] != 1006 for group in ab.values() for a in group)  # codex entries are not abilities

    assert result["discoveries"] == {"map_nodes_by_region_code": {"Her": 1}, "other": {"abyss_gate": 1}}

    items = result["items"]
    assert [i["item_key"] for i in items["abyss_artifact"]] == [75001]
    assert [i["item_key"] for i in items["entry_permit"]] == [801001]
    assert [i["item_key"] for i in items["supply_contract"]] == [1003153]
    assert [i["item_key"] for i in items["quest_item"]] == [75100]


def test_cli_writes_marked_files(tmp_path, capsys):
    out = tmp_path / "out"
    assert candidates.main(["--editor-dir", str(_editor(tmp_path)), "-o", str(out)]) == 0
    assert "Hernand in Chaos" in capsys.readouterr().out
    data = json.loads((out / "abilities.json").read_text(encoding="utf-8"))
    assert data["_status"].startswith("UNVERIFIED")
    assert sorted(p.name for p in out.iterdir()) == [
        "abilities.json", "discoveries.json", "items.json", "main_story.json", "regional_quests.json", "side_quests.json"]


def test_rejects_wrong_folder(tmp_path):
    import pytest
    with pytest.raises(SystemExit, match="not a CrimsonSaveEditor folder"):
        candidates.build(tmp_path)
