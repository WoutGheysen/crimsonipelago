"""Synthetic, structurally valid save files for tests. They contain no game data."""

from __future__ import annotations

import struct

from crimsonipelago.game_adapter import parc, save_container

TYPES = [
    ("SaveRoot", [("_version", "uint32", 1, 4, 0)]),
    ("QuestSaveData", [("_questStateList", "array", 3, 0, 0)]),
    ("KnowledgeSaveData", [("_list", "array", 3, 0, 0)]),
    ("InventorySaveData", [("_items", "array", 3, 0, 0)]),
]


def make_blob(quest_state: int = 3, knowledge: tuple[int, ...] = (1, 2), items: int = 5) -> bytes:
    objects = [
        (0, struct.pack("<I", 1)),
        (1, struct.pack("<II", 1000414, quest_state)),
        (2, b"".join(struct.pack("<I", k) for k in knowledge)),
        (3, struct.pack("<II", 2200, items)),
    ]
    return parc.build_blob(TYPES, objects)


def make_save(**kwargs) -> bytes:
    return save_container.encode(make_blob(**kwargs), save_container.new_header(2))


# --------------------------------------------------------------------------- knowledge layout
# A save with the real *layout* of the knowledge lists (schema, list headers, element framing with
# payload pointers and trailing sizes) as measured on build 25477059, filled with made-up values.
# A quest list after it holds more pointers, which an insert into the knowledge lists has to move.
FF8 = b"\xff" * 8
KNOWLEDGE_TYPES = [
    ("KnowledgeSaveData", [(n, "ReflectObject", 6, 0, 4104) for n in (
        "_list", "_learnableKnowledgeList", "_learnedFollowLearnKnowledge", "_skillLearnSaveDataList",
        "_learnDelaySaveDataList", "_skillPointSaveDataList")]),
    ("KnowledgeElementSaveData", [("_key", "KnowledgeKey", 0, 4, 0), ("_level", "TLevel", 0, 4, 0),
                                  ("_learnedFieldTime", "uint64", 0, 8, 0), ("_isNewMark", "bool", 0, 1, 0)]),
    ("FollowLearnElementSaveData", [("_knowledgeKey", "KnowledgeKey", 0, 4, 0), ("_learnFromTypeRaw", "uint8", 0, 1, 0)]),
    ("SkillPointSaveData", [("_skillPointOwnerType", "SkillPointOwnerType", 2, 1, 0),
                            ("_currentSkillPoint", "TSkillPoint", 0, 2, 0), ("_elapsedSkillPoint", "TSkillPoint", 0, 2, 0)]),
    ("QuestSaveData", [("_questStateList", "ReflectObject", 6, 0, 4104), ("_questSaveVersion", "uint32", 0, 4, 0)]),
    ("QuestStateSaveData", [("_questKey", "QuestKey", 0, 4, 0), ("_state", "uint32", 0, 4, 0),
                            ("_completedTime", "uint64", 0, 8, 0)]),
]


def _list_header(count: int, ones: int) -> bytes:
    return b"\x01" * ones + b"\x00" + struct.pack("<I", count) + b"\x00" * 13


def _element(pos: int, type_index: int, mask: int, payload: bytes) -> bytes:
    head = struct.pack("<HBHB", 1, mask, type_index, 0) + FF8
    po = pos + len(head) + 4
    return head + struct.pack("<II", po, 0) + payload + struct.pack("<I", 4 + len(payload))


def _knowledge_block(start: int, knowledge, learned) -> bytes:
    out = bytearray(struct.pack("<HBI", 1, 0x25, 0))
    out += _list_header(len(knowledge), 0)
    for key, level, when in knowledge:
        out += _element(start + len(out), 1, 0x0F, struct.pack("<IIQB", key, level, when, 1))
    out += _list_header(len(learned), 1)
    for key, src in learned:
        out += _element(start + len(out), 2, 0x03, struct.pack("<IB", key, src))
    out += _list_header(1, 2)
    out += _element(start + len(out), 3, 0x01, struct.pack("<B", 0))
    return bytes(out)


def _quest_block(start: int, quests) -> bytes:
    out = bytearray(struct.pack("<HBI", 1, 0x03, 0))
    out += _list_header(len(quests), 0)
    for key, state, when in quests:
        out += _element(start + len(out), 5, 0x07, struct.pack("<IIQ", key, state, when))
    out += struct.pack("<I", 7)
    return bytes(out)


def make_knowledge_blob(knowledge=((101, 1, 5000), (102, 1, 6000)), learned=((101, 25), (102, 25)),
                        quests=((900, 5, 4000), (901, 5, 4500))) -> bytes:
    sizes = [len(_knowledge_block(0, knowledge, learned)), len(_quest_block(0, quests))]
    probe = parc.build_blob(KNOWLEDGE_TYPES, [(0, b"\0" * sizes[0]), (4, b"\0" * sizes[1])])
    starts = [e.data_offset for e in parc.parse_structure(probe).toc]
    return parc.build_blob(KNOWLEDGE_TYPES, [(0, _knowledge_block(starts[0], knowledge, learned)),
                                             (4, _quest_block(starts[1], quests))])
