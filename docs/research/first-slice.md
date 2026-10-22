# First slice: the opening hours as an Archipelago world

- **Status:** draft for review (2026-10-23). Nothing here is in the live catalogue yet.
- **Covers:** the prologue and chapter 1, from the first cutscene to *Woman in White* (where the story
  hands out Flight).
- **Sources:** 30 saves of the new campaign (the watcher's autosaves from the first evening, the manual saves,
  and the override run E2), mission names and rewards from the game's `missioninfo` and `dropsetinfo` tables,
  and the quest structure from the save editor's quest database.

## How the story is built

Chapters are quests, quests are made of missions (the named story beats, e.g. *In Ashes*), and missions of
objectives (the lines in the quest log, e.g. *Help Naira*). Each has a key and a state in the save; 5 means
completed. A mission completes at the same moment as its last objective, so the checks below are the
**objectives**, named "mission: objective". Abyss puzzles ("challenges") complete separately and are checks
too.

| Chapter | Quest | Missions |
|---|---|---|
| Prologue | Ambush | Unfamiliar Lands, In Ashes |
| Prologue | Unknown Space | Realm of Uncertainty, New Journey |
| 1 | Trials of Kindness | Where Rumors Gather, Mysterious Man, True Wisdom in Kindness, Heart Beyond Borders, Actions Speak Louder than Words |
| 1 | Traces | Mystical Key, Polar Opposites, Abyss without Balance, **Woman in White** |

## Proposed checks (44)

"Seen" = the save series shows it completing (state 5). The two after the jump have not been reached in any
save yet, so their detection still needs one save from there.

| # | Check | Region | Vanilla reward | Seen |
|---|---|---|---|---|
| 1 | Unfamiliar Lands: Search the surroundings | Prologue |  | yes |
| 2 | Unfamiliar Lands: Head to the cabin | Prologue |  | yes |
| 3 | Unfamiliar Lands: Speak with Marius | Prologue |  | yes |
| 4 | Unfamiliar Lands: Speak with Ross | Prologue | Knowledge(Knowledge_Roy, 1) | yes |
| 5 | Unfamiliar Lands: Enter the cabin | Prologue |  | yes |
| 6 | In Ashes: Annihilate the Black Bears | Prologue |  | yes |
| 7 | In Ashes: Help Oongka | Prologue |  | yes |
| 8 | In Ashes: Help Naira | Prologue |  | yes |
| 9 | In Ashes: Help Yann | Prologue |  | yes |
| 10 | In Ashes: Defeat Myurdin | Prologue | Item(CharacterCustomize_Kliff_Neck_Scar, 1), Item(Daeil_Band, 1), Knowledge(Knowledge_Kliff, 2), Knowledge(Knowledge_LinkBand, 1), Knowledge(Knowledge_MainMenuView_All, 1), Knowledge(Knowledge_MainMenuView_Main_Quest, 1), Knowledge(Knowledge_MainMenuView_Main_Skill, 1) | yes |
| 11 | Realm of Uncertainty: Into the Fog | Prologue | Knowledge(Knowledge_Interaction_Start, 1), Knowledge(Knowledge_MainMenuView_QuickSlot_Equip, 1), gameplayvariablechange(Interaction_Unlock,True) | yes |
| 12 | Realm of Uncertainty: Blinding Flash | Prologue | Knowledge(Knowledge_Light_Reflection, 1) | yes |
| 13 | Realm of Uncertainty: Move forward | Prologue | Knowledge(Knowledge_Ziane, 2) | yes |
| 14 | New Journey: Follow Sebastian | Prologue | Item(Inventory_Expansion_2, 1) | yes |
| 15 | New Journey: Head to Hernand | Prologue | Item(Blackberry, 5), Item(Oats, 10), Knowledge(Knowledge_CallVehicle, 1), Knowledge(Knowledge_Faction_Celeste_Hernand, 1), Knowledge(Knowledge_Node_Her_HernandCastle, 2), Knowledge(Knowledge_Node_Her_Temporary_Camp, 2), Knowledge(Knowledge_Node_Her_WycliffeMappery, 2), Knowledge(Knowledge_Sebastian, 2) | yes |
| 16 | Where Rumors Gather: Look around Hernand Tavern | Hernand |  | yes |
| 17 | Where Rumors Gather: Try arm wrestling | Hernand | Item(Money_Copper, 100) | yes |
| 18 | Mysterious Man: Give a coin to the old beggar | Hernand | Item(Alchemist_Key_Proto, 1), Item(Inventory_Expansion_2, 1) | yes |
| 19 | True Wisdom in Kindness: Investigate the sewer | Hernand | Item(Seal_Epistle, 1) | yes |
| 20 | Heart Beyond Borders: Speak with the child | Hernand |  | yes |
| 21 | Heart Beyond Borders: Return the cat to its owner | Hernand | Item(Shay_Cat_Clue, 1) | yes |
| 22 | Actions Speak Louder than Words: Help the distressed villagers | Hernand | Item(Apple, 5), Item(Inventory_Expansion_2, 1), Item(Lentils, 10), Item(Optionary_Hernand_Party_Cloth, 1), Knowledge(Knowledge_Visione_Chip_BossRematch_MarniExcavatron, 1), SubLevelExp(Contribution_Hernandian, 100) | yes |
| 23 | Mystical Key: Examine the key from the old man. | Traces | Item(Alchemist_Key, 1), Knowledge(Knowledge_Alchemist_Key, 1) | yes |
| 24 | Mystical Key: Go to Hernand Castle | Traces |  | yes |
| 25 | Mystical Key: Find the room the key opens | Traces | Knowledge(AbyssGate_0002, 2) | yes |
| 26 | Mystical Key: Go through the shining gate | Traces |  | yes |
| 27 | Axiom Archive: Restore the Abyss | Traces | Knowledge(Knowledge_Node_Abyssone_0002, 3) | yes |
| 28 | Polar Opposites: Activate the Abyss Nexus | Traces |  | yes |
| 29 | Polar Opposites: Go toward the building | Traces | Knowledge(Knowledge_BlackWolf, 1) | yes |
| 30 | Polar Opposites: Find a way into the building | Traces |  | yes |
| 31 | Polar Opposites: Enter the building | Traces | Knowledge(AbyssTransmitter_0002, 1), Knowledge(Knowledge_AbyssBridgeGate, 1) | yes |
| 32 | Abyss without Balance: Go through the skybridge gate to the next Abyss | Traces |  | yes |
| 33 | Abyss without Balance: Activate the Abyss Nexus | Traces |  | yes |
| 34 | Axiom Archive: Obtain the artifact | Traces | Item(Abyss_Artifact, 1) | override run only |
| 35 | Abyss without Balance: Remove the wall blocking the floor | Traces |  | yes |
| 36 | Abyss without Balance: Synthesize the Abyss cell | Traces | Item(Archria_OneHandSword,0,1,1), Knowledge(Knowledge_JiJeongTa_I, 1) | yes |
| 37 | Abyss without Balance: Activate the Abyss power supply | Traces |  | yes |
| 38 | Ethereal Pathway: Restore the Abyss | Traces | Knowledge(AbyssTransmitter_0001, 1), Knowledge(Knowledge_AbyssRuins_AbyssIsland_0001, 1), Knowledge(Knowledge_Node_Abyssone_0001, 3) | yes |
| 39 | Abyss without Balance: Take the Abyss Artifact | Traces |  | yes |
| 40 | Ethereal Pathway: Obtain the artifact | Traces | Item(Abyss_Artifact, 1) | yes |
| 41 | Woman in White: White Crow | Traces |  | yes |
| 42 | Woman in White: Speak with the woman in white | Traces | Knowledge(AbyssGate_0002_AbyssIsland, 1), Knowledge(Knowledge_CrowWing, 1) | yes |
| 43 | Woman in White: Return to Hernand | After the jump |  | not yet |
| 44 | Woman in White: Chapter Transition | After the jump |  | not yet |

Left out on purpose:

- **Missions** (*In Ashes*, *Mystical Key*, …): they complete together with their last objective, so they would
  only double those checks.
- **Abyss Nexus duplicates:** activating a nexus completes a story objective and two challenge entries at the
  same moment; only the story objective is a check.
- The 18 quests completed at the very start (scripted bookkeeping, not gameplay).

## Logic

The story is a straight line: each objective unlocks the next, whatever items you have. The only real gate
in this slice is the jump after *Speak with the woman in white*: the story drops Kliff from a height and
expects him to glide (E2: without Flight, the scripted glide prompt worked once, then he fell to his death).

| Region | Checks | Needs |
|---|---|---|
| Prologue | 1–15 | nothing |
| Hernand | 16–22 | Prologue |
| Traces | 23–42 | Hernand |
| After the jump | 43–44 | Traces **and Flight** |

**Goal:** *Woman in White* completed (mission 1000166).

So in a solo seed Flight lands in one of checks 1–42; in a multiworld it can also be in another player's early
checks, and the player waits at the jump until it arrives.

## Items

| Item | Delivery | State |
|---|---|---|
| Flight | knowledge 40002, added to the save | verified (test D), in the client |
| Abyss Artifact | +1 on the stack in the bag | verified for a stack you already have (test A); a first artifact needs the inventory insert, not built yet |
| Other items (food, money, gear) | inventory insert | not built yet |

The slice has 44 checks and one progression item, so 43 slots need something else. Options, from available
now to more work:

1. **Filler that does nothing in the game** (a "Crow Feather"): works today; boring.
2. **Abyss Artifacts** for every artifact the story gives anyway (checks 34, 40): useful, since artifacts
   upgrade Flight. Works for the stack you already have; the first one needs the inventory insert.
3. **Real items** (money, food, gear, inventory expansions) via an inventory insert: the same kind of
   structural edit as Flight, then one in-game test like test D.

Vanilla rewards stay as they are for now: you still get them from the story. Quest items (the old man's key,
the sewer letter) must stay that way anyway.

## Questions for Wout (next time in the game)

1. **After the jump without Flight**, where did you come back after dying: on the island, or below it? Could you
   still reach Hernand somehow (walking, an Abyss Nexus teleport)? If yes, Flight is not a hard gate there.
2. **Missable?** Can any objective above be skipped for good, e.g. the arm wrestling or the beggar's coin?
3. **Check size:** objectives (44 checks, as above) or only missions (13 checks)?
4. **Filler:** start with option 1 + 2 and add 3 next, or wait for 3 first?
5. Play one save past *Return to Hernand* (any save, the override doesn't matter) so checks 43–44 and the
   goal can be confirmed.

