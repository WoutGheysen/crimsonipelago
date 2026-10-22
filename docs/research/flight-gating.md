# How Flight is gated (and how to make it an Archipelago item)

- **Status:** in progress, 2026-10-22
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
| skill 15036 `Skill_CrowWing_Start` | (nothing points to it) | the take-off action, not a gate |
| item 391518539 `Karanda_Necklace` and three other necklaces | the value 40002 | possibly coincidence; worth one look |

None of these records contains a quest or mission condition, so **the grant lives in the quest or
mission data**, which isn't extracted yet.

## Plan

1. Extract the quest and mission tables (`crimson-data find "*quest*"`, `"*mission*"`, then `extract`)
   and `search` them for 40002 and for 1000546. Expected: the reward of *Abyss without Balance* teaches
   knowledge 40002.
2. **Remove the grant, not the skill:** an overlay group that drops 40002 from that reward. Disabling
   `Skill_CrowWing` itself would need a second override to turn Flight back on, and would risk
   scripted story moments that expect gliding.
3. **Deliver through the save:** when the Archipelago item arrives, add 40002 to both knowledge lists
   (the same path that worked for items in test A).
4. Test D (save only): add 40002 to the 19:00 pre-glider save and check that Kliff can glide. This
   proves the "give" half without any game-data change.

Flight then becomes a **progressive** item: Flight, then Swift Flight and the other upgrades, which
already require 40002 in the game data.
