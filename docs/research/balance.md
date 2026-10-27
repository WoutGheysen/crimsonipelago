# Balance pass 1: the item pool (25 Oct 2026)

Measured on 30 whole-game seeds (`goal: journeys_end`, `include_draft_slices: true`, default categories: the 409
story checks) with `tools/balance_report.py`. Running seeds keep their items: this is for seeds generated after it.

## Before

| Per seed | Count | Share |
|---|---|---|
| 100 Copper | 260 | 63% |
| Crow Feather (does nothing) | 87 | 21% |
| Abyss Artifact | 49 | 12% |
| Breath, Spirit, Vitality of the Abyss | 4 each | 3% |
| Flight, Double Jump | 1 each | |

Five out of six checks gave 100 Copper or nothing. The only items that change how the game plays were two
abilities and the Abyss items.

## After

**Useful (about 20% of checks):**

- Abyss Artifacts, 49 per whole-game seed, unchanged.
- The Abyss stat items, 12, unchanged.
- **The skill tree's movement and Force Palm abilities**, new in slice 2 (8): Vault, Double Vault, Aerial Roll,
  Swift Flight, Aerial Swing, Force Current, Ranged Force Palm and Aerial Force Palm. They're knowledge, delivered
  live like Flight. They're never logic: the story doesn't need them, and the player can still buy them with
  artifacts. Getting one is a skill-tree shortcut.
- **Lesser elixirs**, new: a set of the five in slices 4, 7 and 10 (15 in all).

**Filler (about 80% of checks), now by weight from 20 kinds:**

- copper and silver pouches;
- cooked food (heals);
- arrows (plain, fire, poison, explosive);
- ores for crafting;
- 100 Copper;
- and, rarely, the Crow Feather.

The biggest of these is about 6% of checks.

The result on the same 30 seeds:

- Abyss Artifacts 49;
- 100 Copper 36;
- each of the common filler items 17–25;
- the rare ones about 8;
- every ability once.

**Where the gates land:**

- Flight is always in chapter 1, around check 9 of the story (median).
- Double Jump is in chapter 1 in 70% of seeds and in chapter 2 otherwise (it's needed from *Hernand in Chaos*).

Abilities spread over the whole game.

## What makes it safe

- **All of the new items are drafts.** Seeds without `include_draft_slices` keep the verified pool: 100 Copper and
  Crow Feather as filler, and Abyss Artifacts. Filler is drawn only from items a seed may hold (its slices, and
  drafts only with the option).
- **Every one is a stackable item or knowledge,** the two kinds live delivery handles. Sizes stay under the game's
  stack limits.
- **No unique items** (map pieces, contracts, keys), and no gear: a duplicate of a unique item might be refused, and
  gear isn't delivered live yet (Phase 3).
- **Item keys and stack sizes come from the save editor's item list for this game version.** The first seed that
  delivers each one confirms it, as for the draft checks: the live log shows the game taking it, and the player
  says whether it did what it says.

## Open questions, for the next pass

- **What copper is worth.** 100 Copper and the pouches are guesses at "a small reward". A playthrough tells us
  whether they matter at all, and the weights can follow.
- **How many artifacts the skill tree wants.** 49 over the game is about one per eight checks. If the tree fills
  up long before the end, fewer; if it's starved, more.
- **Whether skill-tree abilities learned as knowledge behave like bought ones,** for example whether the tree shows
  the node as owned. If not, they move to an option.
- **More gates.** The whole game has two progression items. Story abilities from chapter 9 on (Back Hang, Aerial
  Maneuver, Focused Force Palm, Call Dragon) can become gates once their overrides exist, which gives the second
  half of a seed something to wait for.

## 27 Oct: arrows out

In the first multiworld, the game refused **20 Arrows** live (code `0x78c0a4e9`), where pouches, food and ores went
in. Arrows may live outside the main bag, or need a bow. Until that's known:

- the four arrow items are out of the filler pool for new seeds;
- in seeds made before, they're a harmless no-op (`game_ref` kind `nothing`, the old reference kept under `was`), so
  nothing is written into a save for them;
- the client no longer asks the game again every few seconds for an item it refused outright: the item waits for the
  save, once.
