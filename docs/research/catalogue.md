# Catalogue research: the whole story, from the game's data

The first slice (prologue and chapter 1, 44 checks) was built by hand from saves. Growing the world chapter by
chapter needs the same information for every chapter: missions, objectives, what each rewards, and what the
other check categories are. Most of it is in the game's own tables, so it can be read rather than played.

## Reading the story from the game: `crimson-data story`

```powershell
crimson-data story gamedata                                   # chapters known so far (prologue to 5, epilogue)
crimson-data story gamedata --plan research\candidates\main_story.json --json story.json   # every chapter
```

`gamedata` is a folder with `missioninfo` and `dropsetinfo` extracted (`crimson-data extract`). How the records
are laid out (`crimsonipelago/game_data/story.py`):

- `missioninfo` holds missions and objectives alike. After the name comes the **quest** the record belongs to,
  then a **count** and that many **child keys**. A mission lists its objectives in order; an objective has
  none. Unfamiliar Lands lists exactly the first slice's checks 1–5.
- An objective's fields after that: a type (0–7) and its target (what to do: talk to someone, beat a boss, craft
  an item). Then come its **reward entries**: a `dropsetinfo` key, and 12 bytes later the fixed marker
  `73 e1 c5 ea 73 e1 c5 ea ff ff ff ff`. The drop set's name spells the reward out:
  `Knowledge(Knowledge_CrowWing, 1)` is Flight.
- Checked against the 42 checks of the first slice: rewards match on 40. The other two each have one extra
  thing the slice notes counted as a reward, which in the data is the objective's target (the sword crafted in
  *Synthesize the Abyss cell*), and a boss-rematch chip in another field.

Records are found by key, and keys are shared between tables (1001051 is both a mission and a drop set). So a
value is only taken as a reward inside a reward entry, never because it happens to match a key.

## The story so far (game 2.03.02)

Chapters with quest keys known before the full quest database (`docs/research/progression-draft.md`):

| Chapter | Quests | Missions | Objectives (candidate checks) | With a reward |
|---|---|---|---|---|
| Prologue | 2 | 4 | 15 | 7 |
| 1 The First Encounter | 2 | 9 | 25 | 13 |
| 2 Golden Greed | 3 | 18 | 51 | 20 |
| 3 Howling Hill | 3 | 18 | 35 | 16 |
| 4 The Price of Knowledge | 3 | 15 | 36 | 10 |
| 5 Guest Unbidden | 2 | 10 | 23 | 8 |
| Epilogue | 1 | 8 | 15 | 4 |

Chapters 6–12 need their quest keys: `crimson-candidates` writes them (`main_story.json`, from the save
editor's quest database), and `story --plan` then covers every chapter.

## What the story hands out (prologue to chapter 5)

The story's own rewards are the natural item pool: they're what a player of that chapter would have had.

| Chapter | Abilities and unlocks | Items |
|---|---|---|
| Prologue | Call Vehicle (horse), Link Band, Light Reflection, interaction | inventory expansion, food |
| 1 | **Flight** (CrowWing), Abyss bridge gate, Alchemist key, JiJeongTa I | 2 inventory expansions, copper, Abyss Artifact (inactive) |
| 2 | **Double Jump**, **Lift**, **Detect**, **Double Kick**, Abyss teleport scroll, Abyss gear, notice board | **7 inventory expansions**, copper ×4, enchant coins, an Abyss Artifact, a unique sword, ore |
| 3 | sell-price perk, bullet recipe | 2 inventory expansions (III), camp resources, 2 Abyss Artifacts, gloves |
| 4 | Kuku pot, seals | Abyss Artifact, outfit |
| 5 | Crow Feather, Ribentain token | Abyss gear stats and recipe, mace, sword, armour, diamond |

Not item candidates: menu unlocks (`MainMenuView_*`), character entries (`Knowledge_Sebastian`), map nodes
(`Knowledge_Node_*`), boss-rematch chips. Map nodes and Abyss gates can be **checks** instead (discovering them).

## Other check categories

| Category | Count in the data | Detection | Notes |
|---|---|---|---|
| Story objectives | 200 through chapter 5 | mission state (live: Phase 2) | the backbone; one slice per chapter or two |
| Abyss ruins | 100 per region × 5 (codes Her, Dem, Del, Kwe, CD: Hernand, Demeniss, Delesyia, ?, Crimson Desert) | challenge mission state | an option per slice: none, some, all |
| Abyss island ruins | 47 | challenge mission state | goes with Flight |
| Standstone adventures and puzzles | 60 + about 40 | challenge mission state | |
| Abyss challenges | 39 | challenge mission state | two are in the first slice already |
| Regional quests | 35 (12 in Hernand) | quest state | per region |
| Map nodes | 1,111 | knowledge | far too many; towns and landmarks only |

All of these are missions in `missioninfo` (`Challenge_*`), so the same detection the slice uses works for them.

## Items, now that live delivery handles more

| Role | Candidates | Delivery today |
|---|---|---|
| Progression | Flight, Double Jump, Lift, Detect, Double Kick (and the skill-tree traversal abilities: Swift Flight, Aerial Roll, Force Palm…) | knowledge, live (Flight verified; the others need one test each) |
| Useful | inventory expansions, Abyss Artifacts, enchant coins, camp resources, stat items | stackable items, live |
| Filler | copper, food, materials | stackable items, live |
| Later (Phase 3) | weapons and armour rewards | gear isn't accepted live yet |

Whether an ability **gates** anything (needed to reach a check) is the logic question per slice. The first slice
has one gate: the jump after *Speak with the woman in white* needs Flight.

## Proposed slices

| Slice | Covers | Story checks | Plus (options) | New progression |
|---|---|---|---|---|
| 1 (done) | Prologue, chapter 1 | 42 | — | Flight |
| 2 | Chapter 2 *Golden Greed* | about 51 | Hernand Abyss ruins, Hernand regional quests | Double Jump, Lift, Detect |
| 3 | Chapters 3–4 | about 71 | Hernand continued | to find |
| 4 | Chapter 5 and Demeniss | about 23 | Demeniss ruins | to find |
| later | Chapters 6–12, epilogue | from the quest database | per region | |

Each slice is played once before the next (the Phase 1 rule), and its logic is set from that playthrough:
which objectives need an ability, which are missable.

## Needed from the game side

- `research/candidates/main_story.json` and the ability and item lists from `crimson-candidates` (chapters 6–12,
  display names, stack sizes).
- One live test per new ability (`plugin give-knowledge --key K`), as for Flight.
