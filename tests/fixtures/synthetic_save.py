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


# --------------------------------------------------------------------------- inventory layout
# The main bag with "misc" items (money, Abyss Artifacts, ...) in the layout measured on build 25477059:
# five empty sockets, then 01 01, the item key again, charge fields, the time received, the "new" mark.
INVENTORY_TYPES = [
    ("InventorySaveData", [(n, "ReflectObject", 6, 0, 4104) for n in (
        "_inventorylist", "_inventoryChangedItemDataList", "_inventoryHousingGimmickSaveData",
        "_itemSharedCoolTimeDataList", "_restorePreorderItemListXXX")] + [("_platformUniqueId", "PlatformUniqueKey", 0, 4, 0)]),
    ("InventoryElementSaveData", [("_inventoryKey", "InventoryKey", 0, 2, 0), ("_varyExpandSlotCount", "TInventorySlotNo", 0, 2, 0),
                                  ("_itemList", "ReflectObject", 6, 0, 4104)]),
    ("ItemSaveData", [(n, t, k, s, 4104 if k in (5, 6) else 0) for n, t, k, s in (
        ("_saveVersion", "uint32", 0, 4), ("_itemNo", "ItemNo", 0, 8), ("_itemKey", "ItemKey", 0, 4),
        ("_slotNo", "TItemSlotNo", 0, 2), ("_stackCount", "TStackCount", 0, 8), ("_averagePrice", "TStackCount", 0, 8),
        ("_enchantLevel", "TEnchantLevel", 0, 2), ("_useableCtc", "Ctc64", 0, 8), ("_endurance", "TEndurance", 0, 2),
        ("_sharpness", "TEndurance", 0, 2), ("_batteryStat", "TStat", 0, 8), ("_maxBatteryStat", "TStat", 0, 8),
        ("_maxSocketCount", "TSocketSlotNo", 0, 1), ("_validSocketCount", "TSocketSlotNo", 0, 1),
        ("_socketSaveDataList", "ReflectObject", 6, 0), ("_itemDyeDataList", "ReflectObject", 6, 0),
        ("_dropResultSubSaveItemList", "ReflectObject", 6, 0), ("_transferredItemKey", "ItemKey", 0, 4),
        ("_currentGimmickState", "HashCode32", 0, 4), ("_maxChargeUseableCount", "uint32", 0, 4),
        ("_chargedUseableCount", "TickCount64", 0, 8), ("_coolTimePerCharge", "TickCount64", 0, 8),
        ("_timeWhenPushItem", "Ctc64", 0, 8), ("_characterConversionData", "ReflectObjectPtr", 5, 8),
        ("_isNewMark", "bool", 0, 1), ("_isLocked", "bool", 0, 1))]),
    ("ItemSocketSaveData", [("_currentEndurance", "TEndurance", 0, 2, 0), ("_itemKey", "ItemKey", 0, 4, 0)]),
]
MISC_MASK = bytes.fromhex("1f515a01")


def _misc_item(pos: int, item_no: int, key: int, slot: int, stack: int, when: int) -> bytes:
    head = struct.pack("<H", 4) + MISC_MASK + struct.pack("<HB", 2, 0) + FF8
    payload_at = pos + len(head) + 4
    body = bytearray(struct.pack("<I", 0))
    body += struct.pack("<IQIHQHB", 1, item_no, key, slot, stack, 0xFFFF, 5)
    body += _list_header(5, 0)
    for _ in range(5):
        here = payload_at + len(body)
        sock = struct.pack("<HBHB", 1, 0, 3, 0) + FF8
        body += sock + struct.pack("<III", here + len(sock) + 4, 0, 4)
    body += b"\x01\x01" + struct.pack("<IIQQB", key, 1, 1, when, 0)
    return head + struct.pack("<I", payload_at) + bytes(body) + struct.pack("<I", len(body))


def _inventory_block(start: int, items) -> bytes:
    out = bytearray(struct.pack("<HBI", 1, 0x01, 0))
    out += _list_header(1, 0)
    bag_head = struct.pack("<HBHB", 1, 0x07, 1, 0) + FF8
    bag_at = start + len(out)
    payload_at = bag_at + len(bag_head) + 4
    body = bytearray(struct.pack("<I", 0) + struct.pack("<HH", 2, 9))
    body += _list_header(len(items), 0)
    for item_no, key, slot, stack, when in items:
        body += _misc_item(payload_at + len(body), item_no, key, slot, stack, when)
    out += bag_head + struct.pack("<I", payload_at) + body + struct.pack("<I", len(body))
    return bytes(out)


def make_inventory_blob(items=((89, 50001, 0, 14, 1792604706), (1000032, 75001, 1, 3, 1792604800))) -> bytes:
    """A knowledge block (as above) followed by the main bag, so inventory edits move later pointers too."""
    k = _knowledge_block(0, ((101, 1, 5000), (102, 1, 6000)), ((101, 25), (102, 25)))
    types = KNOWLEDGE_TYPES + INVENTORY_TYPES
    base = len(KNOWLEDGE_TYPES)
    sizes = [len(k), len(_inventory_block(0, items)), len(_quest_block(0, ((900, 5, 4000),)))]
    probe = parc.build_blob(types, [(0, b"\0" * sizes[0]), (base, b"\0" * sizes[1]), (4, b"\0" * sizes[2])])
    starts = [e.data_offset for e in parc.parse_structure(probe).toc]
    inv = _inventory_block(starts[1], items)
    # element type indices are schema positions: shift the inventory ones past the knowledge types
    return parc.build_blob(types, [(0, _knowledge_block(starts[0], ((101, 1, 5000), (102, 1, 6000)), ((101, 25), (102, 25)))),
                                   (base, _shift_types(inv, base)), (4, _quest_block(starts[2], ((900, 5, 4000),)))])


def _shift_types(block: bytes, base: int) -> bytes:
    """Element headers above carry inventory-local type indices (1 bag, 2 item, 3 socket); make them global."""
    out = bytearray(block)
    pos = out.find(FF8)
    while pos != -1:
        ti = pos - 3
        struct.pack_into("<H", out, ti, struct.unpack_from("<H", out, ti)[0] + base)
        pos = out.find(FF8, pos + 8 + 4)
    return bytes(out)
