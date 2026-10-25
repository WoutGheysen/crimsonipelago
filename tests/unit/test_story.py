"""The story tree from missioninfo and dropsetinfo (crimson-data story)."""

import json
import struct

from crimsonipelago.game_data import story
from crimsonipelago.game_data.tables import Table


def _table(name: str, records: list[bytes]) -> Table:
    body, index = b"", []
    for rec in records:
        index.append((struct.unpack_from("<I", rec, 0)[0], len(body)))
        body += rec
    header = struct.pack("<H", len(index)) + b"".join(struct.pack("<II", k, o) for k, o in index)
    return Table(name, body, header)


def _named(key: int, name: str, rest: bytes) -> bytes:
    return struct.pack("<II", key, len(name)) + name.encode() + rest


def _mission(key, name, quest, children):
    return _named(key, name, b"\0" + struct.pack("<II", quest, len(children)) +
                  b"".join(struct.pack("<I", c) for c in children) + b"\0" * 16)


def _objective(key, name, quest, rewards=(), decoy=None):
    body = b"\0" + struct.pack("<IIII", quest, 0, 1, 0x98A044) + b"\0" * 20
    if decoy is not None:
        body += struct.pack("<I", decoy) + b"\0" * 12          # a drop set key that isn't a reward entry
    for r in rewards:
        body += struct.pack("<I", 0xEBB4A8E7) + struct.pack("<I", r) + b"\0" * 8 + story.REWARD_MARK + b"\0" * 9
    return _named(key, name, body)


def _game():
    missions = _table("missioninfo", [
        _mission(1000166, "Mission_MeetAlustain_Alustain_Witch", 1000509, [4294964344, 4294964343]),
        _objective(4294964344, "Mission_MeetAlustain_Alustain_Witch_0", 1000509),
        _objective(4294964343, "Mission_MeetAlustain_Alustain_Witch_1", 1000509, rewards=[1014512, 1014513],
                   decoy=1014600),
        _mission(1000200, "Mission_Elsewhere", 777, [4294964000]),
        _objective(4294964000, "Mission_Elsewhere_0", 777, rewards=[1014600]),
    ])
    drops = _table("dropsetinfo", [_named(1014512, "Knowledge(Knowledge_CrowWing, 1)", b"\0" * 8),
                                   _named(1014513, "Knowledge(AbyssGate_0002_AbyssIsland, 1)", b"\0" * 8),
                                   _named(1014600, "Item(Money_Copper, 100)", b"\0" * 8)])
    return missions, drops


def test_story_reads_missions_objectives_and_their_rewards():
    missions, drops = _game()
    nodes = story.parse(missions)
    story.attach_rewards(nodes, missions, drops)
    assert nodes[1000166].quest == 1000509 and nodes[1000166].children == [4294964344, 4294964343]
    assert nodes[4294964343].rewards == ["Knowledge(Knowledge_CrowWing, 1)", "Knowledge(AbyssGate_0002_AbyssIsland, 1)"]
    assert nodes[4294964344].rewards == []                         # the decoy key isn't a reward entry
    tree = story.chapters(nodes, [("Chapter 1", [1000509]), ("Elsewhere", [777])])
    witch = tree[0]["quests"][0]["missions"][0]
    assert [o["key"] for o in witch["objectives"]] == [4294964344, 4294964343]
    lines = story.summary(tree)
    assert lines[1].split() == ["Chapter", "1", "1", "1", "2", "1", "0"]


def test_story_command_takes_the_chapter_plan_from_crimson_candidates(tmp_path, capsys):
    from crimsonipelago.tools.game_data_cli import main

    missions, drops = _game()
    for t in (missions, drops):
        (tmp_path / f"{t.name}.staticinfobody").write_bytes(t.body)
        index = sorted(((r.key, r.offset) for r in t.records.values()), key=lambda kv: kv[1])
        (tmp_path / f"{t.name}.staticinfoheader").write_bytes(
            struct.pack("<H", len(index)) + b"".join(struct.pack("<II", k, o) for k, o in index))
    plan = tmp_path / "main_story.json"
    plan.write_text(json.dumps({"main_story": [{"chapter": "Chapter 1", "title": "The First Encounter",
                                                "quests": [{"quest_key": 1000509}]}]}))
    out = tmp_path / "story.json"
    assert main(["story", str(tmp_path), "--plan", str(plan), "--json", str(out)]) == 0
    tree = json.loads(out.read_text())
    assert tree[0]["chapter"] == "Chapter 1: The First Encounter" and len(tree[0]["quests"][0]["missions"]) == 1
    assert "Chapter 1: The First Encounter" in capsys.readouterr().out


def test_a_plan_with_missions_follows_their_order_and_names_and_reports_missing_ones():
    missions, drops = _game()
    nodes = story.parse(missions)
    story.attach_rewards(nodes, missions, drops)
    plan = [{"chapter": "Chapter 1", "title": "The First Encounter", "quests": [
        {"quest_key": 1000509, "display": "Traces",
         "missions": [{"mission_key": 1000999, "display": "Not in the tables"},
                      {"mission_key": 1000166, "display": "Woman in White"}]}]}]
    tree = story.chapters(nodes, plan)
    quest = tree[0]["quests"][0]
    assert quest["display"] == "Traces" and [m["display"] for m in quest["missions"]] == ["Not in the tables",
                                                                                         "Woman in White"]
    assert quest["missions"][0]["missing"] and len(quest["missions"][1]["objectives"]) == 2
    assert story.summary(tree)[1].split()[-1] == "1"
