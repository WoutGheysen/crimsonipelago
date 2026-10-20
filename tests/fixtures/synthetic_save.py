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
