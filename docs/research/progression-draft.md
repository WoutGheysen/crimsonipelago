# Progression draft: how Crimson Desert maps onto Archipelago

> **Status: draft, unverified.** Built from the NattKh save editor's game-data tables
> (`crimson-candidates`) and published guides, before any save-diff testing. Every key below is a
> *candidate* until a before/after save pair confirms it. Nothing here is in the live catalogue yet.

Regenerate the full candidate lists locally with:

```bash
crimson-candidates --editor-dir ../nattkh/CrimsonSaveEditor      # writes research/candidates/*.json
```

## 1. What the game data gives us

| Source table | Count | Use for Archipelago |
|---|---|---|
| Main-story quests | 39 quests, 168 missions, in 14 groups | Locations, story-order logic, goal |
| Regional quests | 35 (12 in Hernand) | Locations |
| Side quests | 831 (about 220 look internal) | Later: curated locations |
| Knowledge: abilities | 26 traversal, 15 elemental, 11 Blackstar, 29 mount, 53 life skills | Progression and useful items |
| Knowledge: discoveries | 1,111 map nodes, 92 Abyss Gates, 312 Abyss ruins | Locations (curated subset) |
| Items | Abyss Artifacts, 150 Sealed Abyss Artifacts, stat upgrades, unique gear, permits, contracts, map pieces | Locations and useful/filler items |

### Main story structure

`quest_group` 1000–1013 maps one-to-one onto the published chapter list (mission names match):

| Group | Chapter | Main quests (quest key) |
|---|---|---|
| 1000 | Prologue: Dead of Night | Ambush (10001), Unknown Space (1000123) |
| 1001 | 1: The First Encounter | Trials of Kindness (1000027), Traces (1000509) |
| 1002 | 2: Golden Greed | Unexpected Gift (1000004), Hernand in Chaos (10011000), The End of Greed (1000267) |
| 1003 | 3: Howling Hill | Homestead (11006000), The Face Behind the Mask (10029000), Pioneering (1001048) |
| 1004 | 4: The Price of Knowledge | Mysterious Pot (1000142), Daily Life (1001216), Forbidden Knowledge (10040400) |
| 1005 | 5: Guest Unbidden | Uninvited Guest (1000118), Black and White (1000724) |
| 1006 | 6: Cracks in the Shield | Blazing Beacon, Below the Banners, Turning Tides, The Unyielding Shields |
| 1007 | 7: Homecoming | Dawn Mist, Dawn, Decisive Battle, Twisted Fate |
| 1008 | 8: Blood Coronation | Ashen Steps, To Demeniss, Traitor |
| 1009 | 9: The Sage of the Desert | The Calling, Shattered Ties, Thinning Blade, Six Statues and the Beast, Veiled Witch, Enlightenment |
| 1010 | 10: Counterattack | Secret Weapon, Greater Firepower |
| 1011 | 11: Truth and Reality | Brave New World, Foreboding Shadow |
| 1012 | 12: The Abyss | The Final Battle, The Void |
| 1013 | Epilogue: Journey's End | Journey's End (1000358) |

Seven quests show a character name ("Damiane", "Yann", "Kliff") as their data display text; they were
identified by their missions (e.g. 10011000's missions are "For Honor" … "Secrets Hidden in the Dark",
which is *Hernand in Chaos*).

### Quest state values

The editor knows two encodings, and the saves will tell us which one we see:

- `models.QuestState`: `0x0905` in progress, `0x1105` completed, `0x1905` fully completed.
- `quest_deep_parser`: 3 InProgress, 4 CompletionReady, **5 Completed, 6 RewardReceived**.

A separate *RewardReceived* step after *Completed* matters a lot for interception (section 4).

## 2. Locations (checks)

| Kind | Detection | Notes |
|---|---|---|
| Main-story **missions** | `mission_state` (mission key) | 168 fine-grained checks, always in story order |
| Main-story **quests** | `quest_state` (quest key) | 39; the last one is the goal |
| Regional quests | `quest_state` | Region side stories, e.g. *Legendary Wolf*, *The Witch of Wisdom* |
| **Sealed Abyss Artifacts** | item appears / challenge flag | 150 puzzle challenges; ideal "treasure" checks |
| Abyss Gates | `knowledge` key on discovery | 92; also fast travel |
| Bosses | the boss mission's state | 20 of the 39 main-quest internal names contain `Boss` |
| Map nodes | `knowledge` key | 1,111 total: far too many; a curated set of towns/landmarks only |

**Goal:** `Journey's End` (quest 1000358) completed, for the full game. A shorter goal (end of a
chapter) is offered for slice seeds.

## 3. Items

### Progression candidates: traversal abilities (Kliff)

| Ability | Knowledge key | What it gates (to confirm in play) | Normally from |
|---|---|---|---|
| Flight (Crow's Wing glider) | 40002 | Gaps, cliffs, Abyss islands | *Traces* → *Woman in White* (Ch. 1) |
| Swift Flight | 1002349 | Longer glides | Skill tree (Abyss Artifacts) |
| Aerial Roll | 40082 | Air control | Skill tree |
| Double Jump | 1001116 | Ledges | Skill tree (green, 1 artifact) |
| Axiom Force upgrades: Aerial Maneuver / Aerial Swing | 1003512 / 1001711, 1003513 | Grapple traversal | Axiom Force from *Polar Opposites* (Ch. 1) |
| Force Palm | 40086 | Combat, interaction | Early |
| **Focused Force Palm** | 1001756 | **Magic walls hiding rewards** | *Forbidden Knowledge* (Ch. 4), or Ch. 9 if missed |
| Force Current | 1003090 | Mineral deposits, block puzzles | Axiom Force |
| Call Dragon | 1000175 | Free flight, late game | Late story |
| Evasive Roll | 1002710 | Combat (not a gate) | Learned by watching the Hornsplitter boss (Ch. 2) |

Damiane and Oongka have their own copies (`Knowledge_Damian_*`, `Knowledge_Oongka_*`). The first
version should randomize **Kliff only**.

### Useful

- **Abyss Artifact** (item 75001): the skill currency. The natural "useful" item.
- Breath / Spirit / Vitality of the Abyss (19994–19996): permanent stat upgrades.
- Abyss unique gear (`AbyssReward_*`, 13 items).
- Supply contracts (50, e.g. *Equipment Supply Contract – Hernand*): unlock camp vendors.
- Treasure map pieces (17).

### Filler

Copper, consumables, crafting materials.

### Not randomized (first version)

Story-critical quest items, keys that open story doors, the Hernand Entry Permit (until we know
what it gates), dyes, codex entries, map reveals.

## 4. Enforcement: can we hold rewards back?

The big open question (Phase 0, M6), per item type:

| Type | Grant | Withhold | Evidence so far |
|---|---|---|---|
| Ability (knowledge key) | Add key | Remove key after the game grants it | Adding keys works: the editor's knowledge packs unlock skills ("most, not all"). **Removing is untested.** The editor warns some effects (map reveal) can't be undone by key edits. |
| Abyss Artifact / stackable | Raise count | Lower count after a check | Inventory editing is the editor's core feature. |
| Unique gear | Add item | Remove item | Same. |
| Quest reward in general | — | Possibly by keeping the quest at *Completed* instead of *RewardReceived* | Pure hypothesis from the state list. If true, this is the cleanest hook. |

The save-file approach writes only while the game is closed, so a withheld reward is removed and
a received one added **when you quit to desktop**. Between earning and quitting you'd still have
it. Acceptable for a first version; a runtime mod would remove that gap later.

### Story-safety rule

If a later mission *requires* an ability (for example a scripted glide), removing it could softlock
the story. So:

1. Every randomized ability gets a logic rule: story missions that need it require the item.
2. Abilities the story needs that the logic can't model stay vanilla (not randomized).
3. The client never removes an ability while the current mission needs it (to be detected from
   the mission states).

## 5. First playable slice: "Hernand" (Prologue → Chapter 2)

A small, complete seed to build and test end to end before the full game:

| | Contents | Approx. count |
|---|---|---|
| Locations | Missions of the Prologue, Ch. 1, Ch. 2 | 29 |
| | Their main quests | 7 |
| | Hernand regional quests | up to 12 |
| | Sealed Abyss Artifacts and Abyss Gates in Hernand | to be counted in play |
| Progression items | Flight, Axiom Force upgrade, Force Palm, Double Jump (Evasive Roll as useful) | 4 |
| Useful items | Abyss Artifacts, stat upgrades, Hernand supply contracts | fills the rest |
| Goal | *The End of Greed* (quest 1000267), the end of Chapter 2 | |

## 6. Open questions for testing

1. Which state values mark a completed mission and quest? Is there a RewardReceived step?
2. Does Flight appear as knowledge key 40002 at *Woman in White*?
3. If key 40002 is removed from a save, can Kliff still glide after reloading?
4. Does adding one Abyss Artifact (item 75001) to a save survive a reload and an in-game save?
5. Which Ch. 1–2 missions physically require Flight or Axiom Force?
6. Is the Hernand Entry Permit a hard gate anywhere?

## Sources

- NattKh save editor data tables (MPL-2.0), read locally from the user's checkout:
  `quest_database.json`, `mission_names.json`, `knowledge_keys_all.json`, `item_names.json`,
  `models.py`, `quest_deep_parser.py`, `parc_inserter3.py`.
- [Shacknews: Crimson Desert main mission list](https://www.shacknews.com/article/148382/crimson-desert-main-mission-list)
- [The Games Wiki: Exploration and traversal](https://thegameswiki.com/crimson-desert/wiki/exploration-and-traversal)
- [Nerdschalk: Flight vs Swift Flight vs Aerial Roll](https://nerdschalk.com/crimson-desert-flight-vs-swift-flight-vs-aerial-roll-unlock-and-uses-explained/)
- [games.gg: Crimson Desert skills guide](https://games.gg/crimson-desert/guides/crimson-desert-skills-guide/)
