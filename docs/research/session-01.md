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

**Completing *Abyss without Balance* (meeting White Crow) grants, in one save:**

| Key | Internal | Display |
|---|---|---|
| **40002** | Knowledge_CrowWing | **Flight** (the glider) |
| 40086 | Knowledge_JiJeongTa_I | Force Palm |
| 1000929 | Knowledge_JiJeongTa_Cure | Healing Force Palm |
| 1001117 | Knowledge_JiJeongTa_Air | Aerial Force Palm |
| 1001093 | Knowledge_WhiteCrow_Witch | White Crow (codex) |

plus +1 Abyss Artifact (objective "Take the Abyss Artifact") and the Abyss challenge mission
1000066 *Ethereal Pathway* (→ 5). *Woman in White* (1000166) becomes available at the same moment,
so the glider comes with *Abyss without Balance*, not *Woman in White*. Confirmed from the save folder
(slot101, 19:19).

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

## Save folder layout

```text
<account>/slot0..slot2/   autosave rotation, about every 15 minutes
<account>/slot100+/       other saves (slot101: a save made right after the glider; slot100: an old campaign)
<account>/slotN/lobby.save  small LobbySaveData: save-menu summary
```

`lobby.save` (same container format) holds `_generateNo`, `_slotSaveData._generatedTime` (Unix time of
the write), `_characterKey`, `_level`, the main quest and mission shown in the menu, and
`_customDisplayName` ("Woman in White").

## Consequence for the client: campaign identity

The client binds a seed to a save *profile*, currently `account/slotN`. With slot rotation, the next
autosave would look like a different profile and pause sync. The client must recognise the
**campaign**, not the slot.

Leads:

1. `_timeWhenPushItem` on items: the upper 32 bits are Unix time. All starting items of the new
   campaign carry 0x6ad8fa22 = **2026-10-21 17:45 local**, the moment the new game started; the old
   campaign's carry 0x69e7c341 = 2026-04-21, matching slot100. The earliest push time in a save is
   effectively the campaign's creation time.
2. `lobby.save` `_generateNo`: 1000033 in all four slots of the new campaign, 79 in the old one.
   Meaning unknown.

To confirm lead 1:

- It stays constant over a whole session and across slots (true so far: slot0 → slot2).
- It differs between two new campaigns (needs a second new game).
- It survives the starting items being sold or used up (the earliest push time in the save may be
  the better signal).

## Write tests (in progress)

Made with `crimson-testsave` from slot101 (in-place, same-size edits; verified by re-reading):

| Copy | Change | Question |
|---|---|---|
| A | Abyss Artifact 75001: 2 → 3 | Does the game load it, show 3, and keep 3 after its own save + reload? |
| B | knowledge 40002 → 1006 (Flight replaced by a codex entry) | Does the game load it, and can Kliff still glide? |

**Results:**

- **A: passed.** The edited save loads, shows 3 Abyss Artifacts, and still shows 3 after the game's own
  manual save and a restart. *The game accepts our re-encoded saves and keeps the edit*: item delivery
  through the save works.
- **B: Flight came back.** The save loads (2 artifacts, so it really was B), but Kliff can still glide
  and the skill tree shows Flight learned (Lv 1/3). The game's next save contains 40002 *and* the
  placeholder 1006 (77 entries), so it **re-added Flight on load**.

Flight is stored three times in the save:

| Where | Entry |
|---|---|
| `KnowledgeSaveData._list[]` | `_key 40002, _level 1, _learnedFieldTime, _isNewMark` |
| `KnowledgeSaveData._learnedFollowLearnKnowledge[]` | `_knowledgeKey 40002, _learnFromTypeRaw 25` |
| `ContentsMiscSaveData` (undecoded range, next to `_alertHistorySaveDataList`) | likely the "learned" notification history |

`_learnFromTypeRaw` seen: 25 for story grants (Blinding Flash, Summon Mount, Axiom Force, Flight, UI
unlocks), 15 for Force Palm, 20 for Healing and Aerial Force Palm, 0 and 1 for others.

- **C: Flight came back again.** Removed from both `_list` and `_learnedFollowLearnKnowledge`; the game
  re-added it to both on load, with a fresh learned timestamp and no quest change. Story-granted
  abilities are re-granted on load; see [flight-gating.md](flight-gating.md) for the follow-up.

## Next

1. Record the results of write tests A and B.
2. Campaign identity: implement and test the creation-time fingerprint, then bind seeds to it.
