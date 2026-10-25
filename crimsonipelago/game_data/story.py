"""The story's structure from the game's own tables: quests -> missions -> objectives, with their rewards.

`missioninfo` holds missions and their objectives alike. After a record's name (u32 key, u32 length, name, one
byte) comes the quest it belongs to (u32), then a u32 count and that many child keys: a mission lists its
objectives in order; an objective has none. Rewards are references from a record to `dropsetinfo` records whose
names spell the reward out (`Item(Money_Copper, 100)`, `Knowledge(Knowledge_CrowWing, 1)`). An objective can
be followed by challenge missions (Abyss puzzles), which complete separately: candidate checks of their own,
named Challenge_*.

Worked out on game 2.03.02 against the first slice (docs/research/first-slice.md): the Unfamiliar Lands
mission lists exactly checks 1-5, and Woman in White's second objective rewards Knowledge_CrowWing (Flight).
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

from .tables import Table

REWARD = re.compile(r"^(Item|Knowledge|SubLevelExp|gameplayvariablechange|Exp|Money)\(", re.IGNORECASE)

# Main-story quests per chapter, as far as known without the save editor's quest database
# (docs/research/progression-draft.md). The full list comes from crimson-candidates' main_story.json.
KNOWN_CHAPTERS: list[tuple[str, list[int]]] = [
    ("Prologue", [10001, 1000123]),
    ("Chapter 1", [1000027, 1000509]),
    ("Chapter 2", [1000004, 10011000, 1000267]),
    ("Chapter 3", [11006000, 10029000, 1001048]),
    ("Chapter 4", [1000142, 1001216, 10040400]),
    ("Chapter 5", [1000118, 1000724]),
    ("Epilogue", [1000358]),
]


@dataclass
class Node:
    key: int
    internal: str
    quest: int
    children: list[int]
    rewards: list[str] = field(default_factory=list)


def parse(table: Table) -> dict[int, Node]:
    nodes: dict[int, Node] = {}
    for key, rec in table.records.items():
        data = rec.data
        if len(data) < 8:
            continue
        p = 8 + struct.unpack_from("<I", data, 4)[0]
        if p + 9 > len(data):
            continue
        quest, count = struct.unpack_from("<II", data, p + 1)
        if count > 64 or p + 9 + 4 * count > len(data):
            continue
        nodes[key] = Node(key, rec.name, quest, list(struct.unpack_from(f"<{count}I", data, p + 9)))
    return nodes


REWARD_MARK = bytes.fromhex("73e1c5ea73e1c5eaffffffff")   # ends every reward entry; the drop set key is 12 bytes before


def attach_rewards(nodes: dict[int, Node], missions: Table, drops: Table) -> None:
    """Each record's rewards: the drop set keys in its reward entries (a key, then REWARD_MARK 12 bytes on)."""
    for key, node in nodes.items():
        data = missions.records[key].data
        found: list[str] = []
        at = data.find(REWARD_MARK)
        while at != -1:
            if at >= 12:
                drop = drops.records.get(struct.unpack_from("<I", data, at - 12)[0])
                if drop is not None and drop.name and drop.name not in found:
                    found.append(drop.name)
            at = data.find(REWARD_MARK, at + 1)
        node.rewards = found


def chapters(nodes: dict[int, Node], plan: list | None = None) -> list[dict]:
    """Per chapter: its quests, their missions and objectives. `plan` is either (name, [quest keys]) pairs, or the
    chapters of crimson-candidates' main_story.json (then missions follow the story's order and carry their
    display names)."""
    by_quest: dict[int, list[Node]] = {}
    for n in nodes.values():
        if n.children:
            by_quest.setdefault(n.quest, []).append(n)
    out = []
    for entry in plan or KNOWN_CHAPTERS:
        if isinstance(entry, dict):
            name = f"{entry.get('chapter')}: {entry.get('title', '')}".rstrip(": ")
            quests = [(int(q["quest_key"]), q.get("display"),
                       [(int(m["mission_key"]), m.get("display")) for m in q["missions"]] if q.get("missions") else None)
                      for q in entry.get("quests", [])]
        else:
            name, keys = entry
            quests = [(q, None, None) for q in keys]
        chapter = {"chapter": name, "quests": []}
        for q, q_display, listed in quests:
            if listed is None:
                listed = [(m.key, None) for m in sorted(by_quest.get(q, []), key=lambda n: n.key)]
            missions = []
            for key, m_display in listed:
                m = nodes.get(key)
                if m is None:
                    missions.append({"mission_key": key, "display": m_display, "missing": True, "objectives": []})
                    continue
                objs = [nodes[c] for c in m.children if c in nodes]
                missions.append({"mission_key": m.key, "internal": m.internal, "display": m_display,
                                 "rewards": m.rewards,
                                 "objectives": [{"key": o.key, "internal": o.internal, "rewards": o.rewards}
                                                for o in objs]})
            chapter["quests"].append({"quest_key": q, "display": q_display, "missions": missions})
        out.append(chapter)
    return out


def summary(story: list[dict]) -> list[str]:
    lines = [f"{'chapter':<40} {'quests':>6} {'missions':>8} {'objectives':>10} {'rewarded':>8} {'missing':>7}"]
    for ch in story:
        missions = [m for q in ch["quests"] for m in q["missions"]]
        objs = [o for m in missions for o in m["objectives"]]
        missing = sum(1 for m in missions if m.get("missing"))
        rewarded = sum(1 for o in objs if o["rewards"])
        lines.append(f"{ch['chapter'][:40]:<40} {len(ch['quests']):>6} {len(missions):>8} {len(objs):>10} "
                     f"{rewarded:>8} {missing:>7}")
    return lines
