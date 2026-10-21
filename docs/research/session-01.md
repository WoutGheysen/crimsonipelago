# Research session 1: Prologue and Chapter 1

- **Date:** 2026-10-21
- **Game build:** Steam build id `25477059` ("Crimson Desert Enhanced")
- **Saves:** 24 save copies and snapshots, 17:51–19:00, plus the save folder afterwards (see below)
- **Tracker:** `crimson-inspect watch` (single-file version, see "Save slot rotation")

## Confirmed

### Quest and mission states

Quests and missions use the 0–6 encoding (`quest_deep_parser`): **2 = Available, 5 = Completed**,
1 = Locked. Completion also sets `_completedTime`. Seen directly:

| Key | Name | Change |
|---|---|---|
| 10001 | Ambush (quest) | 2 → 5 |
| 1000160 | In Ashes (mission) | 2 → 5 |
| 1000123 | Unknown Space (quest) | 2 → 5 |
| 1000620, 1000164 | Realm of Uncertainty, New Journey | → 5 |
| 1000027 | Trials of Kindness (quest) | 2 → 5 |
| 1000052, 1000053, 1000294, 1000042, 1000051 | its five missions | → 5 |
| 1000043 | Mystical Key | 2 → 5 |
| 1000048 | Polar Opposites | 2 → 5 |
| 1000509 | Traces (quest) | → 2 (in progress at 19:00) |

The data-table mission keys from `crimson-candidates` matched the save in every case.
**Detection rule for story checks: `state == 5`.** No state 6 (RewardReceived) has been seen yet.

Objective-level sub-steps use keys near `2^32` (e.g. 4294964428 "Help Naira"). They are transient
objectives, not checks.

### Abilities (knowledge keys)

| Key | Internal | Display | Granted |
|---|---|---|---|
| 40039 | Knowledge_Light_Reflection | Blinding Flash | Prologue (with mission 1002799) |
| 1000490 | Knowledge_LinkBand | Axiom Bracelet | end of *Ambush* |
| 1000174 | Knowledge_CallVehicle | Summon Mount | start of Chapter 1 |
| **40068** | **Knowledge_BlackWolf** | **Axiom Force** | during *Polar Opposites* (objective "Go toward the building") |

Axiom Force's internal name could not have been guessed from the data tables, so confirmation in a
real save was essential.

Flight (40002, the glider) came around 19:15, after the old tracker stopped following the save;
it will be confirmed from the save folder.

### Items

| Key | Item | Seen |
|---|---|---|
| 75001 | Abyss Artifact | +1 at 18:53:45 (Alchemist's Chamber chest, *Mystical Key*) |
| 75002 | Faded Abyss Artifact | +1 at the same moment |

Items live in `InventorySaveData._inventorylist[]` (bag 2 = main bag, bag 5 = quest and key items),
each `_itemList[]` entry with `_itemKey` and `_stackCount`.

## Problems found and fixed

1. **Save slot rotation.** The game rotates its saves over slots: slot0 until 18:04, slot1 at
   18:15, slot2 at 18:25, and another slot after 19:00. A tracker watching one file loses the session.
   Fixed: `watch` now follows every slot.
2. **Inventory blind spot.** The editor's legacy item scanner saw about 11 item kinds while the
   inventory grew from 801 to 6,064 bytes. Fixed: items are read from the parsed
   `InventorySaveData` tree (snapshot format 2).
3. **Unstable field paths.** `--fields` paths numbered objects by position, which shifts as the game
   adds save objects. Fixed: objects are numbered per class.
4. **Misleading schema note.** New save data types appearing (normal progress) was reported as
   "different game build or a corrupted save". Fixed.

## Consequence for the client: campaign identity

The client binds a seed to a save *profile*, currently `account/slotN`. With slot rotation, the next
autosave would look like a different profile and pause sync. The client must recognise the
**campaign**, not the slot.

Lead: every starting item in the new campaign has the same `_timeWhenPushItem`
(`0x00006ad8fa220000` as stored), and the older campaign found in slot0 has a different one
(`0x000069e7c3410000`). That looks like a campaign-creation timestamp. To confirm:

- It stays constant over a whole session and across slots (true so far: slot0 → slot2).
- It differs between two new campaigns (needs a second new game).
- It survives the starting items being sold or used up (the earliest push time in the save may be
  the better signal).

## Next

1. Confirm Flight (40002) and *Woman in White* (1000166) from the save folder.
2. First write tests on copies of the post-glider save: remove 40002; add one Abyss Artifact.
3. Campaign identity: implement and test the fingerprint, then bind seeds to it.
