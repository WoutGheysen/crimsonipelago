# Ruleset draft: goals, options, logic, item pool

- **Status:** first draft (25 Oct 2026), for discussion. Nothing here is built beyond what the first slice and the
  slice-2 draft already have. It follows the catalogue research (`catalogue.md`) and how comparable Archipelago worlds
  set themselves up.
- **Rule:** an option only appears once what it controls works in the game (as now: `goal`, `item_pool_mode`,
  `included_check_categories`).

## What other worlds do (and what we take)

| World | What it does | What we take |
|---|---|---|
| Dark Souls III | ~1,500 locations. Groups that could hide progression badly (missable, hidden, small items) never hold it by default (`exclude_locations`). Late regions can be excluded to shorten the route. One goal (four Cinders, then the final boss). Smooth distribution: weaker upgrades earlier. | Missable checks never hold progression. Regions/chapters can be cut to shorten seeds. Smoothing of Abyss Artifacts. |
| Jak and Daxter | Several goals (each a story milestone, or open). Movement randomizer as an option: off = normal moves. Gate counts (power cells) as options, with caps so multiworlds stay fair. Collectible bundles as optional checks (orbsanity). Traps replace filler only. | Goal per story milestone. Ability randomization as an option. Optional collectible checks (Abyss ruins). Traps later, filler only. |
| Hollow Knight (known) | Each check category its own toggle; movement split into separate items; skips as options. | Category toggles (we have them). No skips at first. |
| Archipelago itself | `progression_balancing`, `accessibility`, `start_inventory_from_pool`, `local_items`, `early_items`, `exclude_locations`, hints | Use these instead of inventing our own (e.g. `early_items: Flight: 1` for solo seeds). |

## Goals

The story is the spine: every slice ends at a chapter's end, and that's a goal.

| Goal | Ends at | Slice | State |
|---|---|---|---|
| `first_slice` | *Speak with the woman in white* (chapter 1) | 1 | offered |
| `chapter_2` | *Cheers Echoing From the Edge* | 2 | draft |
| `chapter_N` | each chapter's last mission | 3+ | as slices come |
| `journeys_end` | *Journey's End* (the epilogue) | last | the full game |

Later, a **hunt** goal (as DS3's Cinders, KH2's emblems): collect N of a goal item spread over the multiworld, then
finish the current slice's last mission. It evens out seed length in multiworlds with fast games.

## Check categories (`included_check_categories`)

Today: `quest, boss, discovery, treasure, ability, exploration`. Mapping the game onto them:

| Category | Crimson Desert | Count | Detection | Default |
|---|---|---|---|---|
| `quest` | story objectives | 405 | mission state (proven) | on |
| `boss` | boss objectives (a subset of the story, e.g. *Defeat Myurdin*) | ~20 | mission state | on |
| `exploration` | Abyss ruins, island ruins, standstone puzzles, Abyss challenges | 860 | challenge mission state | **per region**, off at first |
| `treasure` | Sealed Abyss Artifacts (puzzle chests), treasure maps | 150 + 17 | item appears / challenge | off at first |
| `discovery` | Abyss gates, landmarks | 92 + 8 | knowledge key | off at first |
| `ability` | learning a skill-tree ability | — | knowledge key | off |
| (new) `regional` | regional quests (the Witches, Legendary Wolf…) | 35 | quest state | on, per region |

860 exploration checks is too many to switch on wholesale. Better: an option **`abyss_ruins: none / some / all`**
per region in the seed's slices, where `some` is the ones nearest the story path (to choose in playtests).

## Missable and risky checks

- `missable: true` on a check means it **never holds progression** (DS3's rule), only filler or useful.
- Objectives skipped by finishing their mission count when the mission does (`parent_mission_key`), so skipping
  isn't missing.
- The playthrough of each slice marks the truly missable ones.

## Items

### Progression: movement

Story-granted abilities stay story-shaped (`slice-2.md`): each needs the override that stops the story teaching it,
and logic requires it from the point the story would have taught it.

| Option `randomize_abilities` | In the pool | Notes |
|---|---|---|
| `story` (default) | the slice's story abilities: Flight, Double Jump; later Back Hang, Aerial Maneuver, Focused Force Palm, Call Dragon | needs one override for all |
| `story_and_skill_tree` | + skill-tree movement: Vault, Double Vault, Aerial Roll, Swift Flight, Aerial Swing, Force Current, Ranged and Aerial Force Palm | the skill tree then can't sell them: to check (does the tree respect a learned ability?) |
| `off` | none; the story teaches as usual | for players who only want items shuffled |

**Progressive items** where the game has tiers, so a seed can't hand out the upgrade before the base (the HK way
splits, the common AP way chains):

- Progressive Flight: Flight → Swift Flight
- Progressive Vault: Vault → Double Vault
- Progressive Force Palm: Force Palm → Focused → Ranged / Aerial

### Useful

| Item | Why | Count per slice (first guess) |
|---|---|---|
| Abyss Artifact | the skill tree: carries chapters 2–9, where the story adds no movement | 3 in slice 1; ~1 per 8 checks after |
| Inventory expansion | the story hands out 11 by chapter 3; a full bag is a real pain | the story's own count, moved into the pool |
| Breath / Spirit / Vitality of the Abyss | permanent stamina, spirit, health | a few per slice |
| Supply contracts | unlock a region's camp vendor | the slice's region(s) |
| Treasure map pieces | in sets: only useful complete | whole sets, late slices |

**Smoothing** (DS3): Abyss Artifacts spread through the spheres rather than clumped late, so the skill tree keeps
pace with the story.

### Filler and traps

- Filler: Copper (in sensible amounts per slice: chapter 2 hands out 100–1,000), food, materials. "Crow Feather"
  (nothing) stays as last resort.
- Traps: later, and only replacing filler (Jak's rule). Ideas that need nothing new from the plugin: none yet;
  they'd need live effects (a debuff, an ambush). Not before the core is solid.

### Not randomized (first version)

Keys and story items (softlock risk), gear (until Phase 3), dyes, outfits, codex and map knowledge.

## Logic rules

1. **Story order.** Each slice's regions follow its quests in order; a region needs the previous one.
2. **Ability gates, conservative.** From the point the story would teach an ability, everything later requires it.
   Playtests relax a gate only when the slice is played through without the ability.
3. **No skips** (no glitches, no "hard" movement). Maybe an option much later.
4. **Missable checks never hold progression.**
5. **Solo seeds:** `early_items` for Flight when the slice's jump would otherwise stall the start (to judge in the
   balance pass).

## Options at a glance (target for the first public version)

| Option | Values | Default | Needs |
|---|---|---|---|
| `goal` | first_slice, chapter_2, …, journeys_end | the latest verified slice | slices verified |
| `randomize_abilities` | story, story_and_skill_tree, off | story | the override per ability; live tests |
| `included_check_categories` | quest, boss, regional, exploration, treasure, discovery | quest, boss, regional | detection per category |
| `abyss_ruins` | none, some, all | none | the ruins' detection; "some" chosen in play |
| `item_pool_mode` | conservative, expanded | expanded | (exists) |
| `death_link` | on, off | off | death detection (dying doesn't reload: r6-edges) and a way to kill Kliff |
| plus Archipelago's own | progression balancing, accessibility, early/local items, start inventory, exclusions | AP defaults | nothing |

## Open questions for the balance pass

- How many checks per hour does a slice give? The fresh-seed playthrough measures it for slice 1 (42 checks). It
  sets how many useful items a slice can afford before it feels like filler.
- Does the skill tree respect abilities learned from Archipelago (can it sell them again)?
- Which Abyss ruins are on or near the story path in each region?
- How long does a player wait at the Flight jump in a typical multiworld, and is `early_items` needed?
