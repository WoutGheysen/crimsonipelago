# How Flight is gated (and how to make it an Archipelago item)

- **Status:** the override withholds Flight in-game (E2 passed); test D (give it back through the save) built, awaiting test (2026-10-22)
- **Build:** 25477059
- **Inputs:**
  - write tests A, B and C (session 1)
  - Wout's findings document, "Crimsonipelago Flight Reverse Engineering Findings" (21 October)
  - the extracted skill, skill-tree and knowledge tables

## What the saves showed

| Test | Change | Result |
|---|---|---|
| A | +1 Abyss Artifact | Loads, shows, and survives the game's own save. **Item delivery works.** |
| B | Flight (40002) removed from `KnowledgeSaveData._list` | Can still fly; the game's next save has 40002 again |
| C | 40002 removed from `_list` **and** `_learnedFollowLearnKnowledge` | Can still fly; 40002 is back in both, with a **new** learned timestamp and no quest change |
| (Wout) | Flight knowledge level 1 → 0, key → 0, quest rollbacks of *Trials of Kindness* / *Traces* | Can still fly |
| (Wout) | Pre-glider save (no 40002) | Cannot fly |

Conclusion: **having 40002 is what enables Flight, and the game grants 40002 afresh on load** once the
story says you have it. The grant comes with *Abyss without Balance* (mission 1000546; seen in
session 1). That mission stayed completed in every test, including Wout's rollbacks, which touched
*Trials of Kindness* (1000027) and the parent quest *Traces* (1000509).

## What the game tables show

From `skill.pabgb`, `knowledgeinfo.pabgb` and `skilltreeinfo.pabgb` (`crimson-data search` / `dump`):

| Record | Points to | Reading |
|---|---|---|
| skill 15027 `Skill_CrowWing` | knowledge 40002 (offset 120) | the skill needs Flight knowledge |
| knowledge 40002 `Knowledge_CrowWing` | skill 15027 (offset 40) | learning Flight enables the skill |
| knowledge 40002 | item 75001 Abyss Artifact (offsets 410, 694) | upgrade costs (Lv 2, Lv 3), matching the in-game "Abyss Artifact ×1" |
| knowledge 40002 | knowledge 1002349 Swift Flight (offset 452) | level 2 grants Swift Flight |
| knowledge 1002349 Swift Flight, 40082 Aerial Roll, 1004772 Super Dash, 1005740 Enhanced | knowledge 40002 | they require Flight |
| skilltree 9 `SpecialSkill_Kliff`, 50 `Skill_Kliff` | skill 15027 | Flight's place in Kliff's skill tree |
| skill 15036 `Skill_CrowWing_Start` | (nothing points to it) | the take-off action; it checks the gate (see below) |
| item 391518539 `Karanda_Necklace` and three other necklaces | the value 40002 | possibly coincidence; worth one look |

None of these records contains a quest or mission condition. The rest of the chain is in the
quest/mission data, found with `crimson-data search` over all 134 tables (2026-10-22):

| Record | Content | Role |
|---|---|---|
| missioninfo 4294964343 `Mission_MeetAlustain_Alustain_Witch_1` | rewards: drop sets 1014512 and 1014513 | the objective "Speak with the woman in white" (the save shows it completing as the glider appears) |
| dropsetinfo 1014512 `Knowledge(Knowledge_CrowWing, 1)` | 40002 at offsets 59 and 118, plus the text twice | **the grant** |
| dropsetinfo 1014513 `Knowledge(AbyssGate_0002_AbyssIsland, 1)` | | the objective's second reward, an Abyss Gate |
| conditioninfo 1000547 `checkCharacterKey(Kliff) && checkKnowledge(Knowledge_CrowWing)` | 40002 at offset 97, plus the text | **the gate** |
| skill 15036 `Skill_CrowWing_Start` | uses condition 1000547 (offset 126) | the take-off action checks the gate |
| missioninfo 1000546 `Mission_MeetAlustain_Alustain_AbyssGate` | sub-missions `_0`…`_5` | *Abyss without Balance* and its steps |

So `Skill_CrowWing_Start` is where the check happens. It isn't referenced by other records, but it
references the condition. The earlier note that it is "not a gate" was wrong.

## Plan

1. ~~Find the grant in the quest/mission tables.~~ Done (above).
2. **Remove the grant, not the skill:** `crimson-mod install flight-grant-removed` patches drop set
   1014512 to teach the Hedgehog codex entry (1006) instead: same size, same-length text. Fallback if the
   on-load re-grant has another source: point condition 1000547 at a knowledge key the game never
   grants, and deliver that one.
3. **Deliver through the save:** when the Archipelago item arrives, add 40002 to both knowledge lists
   (the same path that worked for items in test A).
4. Test D: give 40002 back in a save made with the override active, and check that Kliff can glide.
   This proves the "give" half (see below).

Flight then becomes a **progressive** item: Flight, then Swift Flight and the other upgrades, which
already require 40002 in the game data.

## In-game tests of the override

Packing: the first build (LZ4, simple name table, group `0090`) crashed the game at boot. The packer now
writes the community tools' layout byte for byte (radix name table, uncompressed, next group number).
`diagnostic-noop` (an unchanged copy of the table) then booted, and so did `flight-grant-removed`.

**E2, story: passed.** With `flight-grant-removed` installed, Wout played from manual save 2 through the
woman in white and made manual save 6 (`slot105`) afterwards:

| Check | Result |
|---|---|
| *Speak with the woman in white* (Witch_1) | completed (5) |
| *Abyss without Balance* | completed (5) |
| Flight 40002 in `_list` / `_learnedFollowLearnKnowledge` | **absent** from both |
| Hedgehog 1006 | present in both, `_learnFromTypeRaw` 25 (story grant): the patched reward fired |
| In-game | the scripted "press to glide" prompt during the jump worked once; after that no gliding (Kliff died from a fall) |

So the grant is the drop set, and the override withholds it. The one scripted glide is part of the cutscene
sequence, not the skill.

**Design consequence:** the story drops Kliff from a height right after this objective and expects him
to glide. The logic must place Flight before that point is reachable (or treat the area after the jump
as requiring Flight), otherwise the seed can strand the player.

**E1, reload (optional):** load the test-C save with the override installed. Not needed for the design
any more: E2 already shows the story grant goes through this drop set.

## Test D: give Flight through the save (built)

Made from save 6 above (override active, no Flight). The Hedgehog placeholder is turned into Flight in both
knowledge lists (`crimson-testsave --swap-knowledge 1006:40002`), which is what the client will do when
the Archipelago item arrives. Expected: Kliff can glide after loading, and still after a save, a restart
and a reload.
