# Draft chapters: playing the whole game before it's all verified

- **Status (25 Oct 2026):** the whole story is in the catalogue. Slice 1 (prologue, chapter 1: 42 checks) is
  verified; slices 2–13 (chapter 2 to the epilogue: 367 checks) are drafts.
- **Why drafts are playable:** every draft check is a story objective read from the game's own tables
  (`crimson-data story`), detected from the save exactly like the verified ones (its mission state, or its mission's
  completion). The keys come from the game, so they're very likely right. What isn't known yet is whether each
  fires when expected, and whether the story needs an ability earlier than the logic assumes.

## Playing them

In the player options:

```yaml
goal: journeys_end            # or chapter_2 ... chapter_12
include_draft_slices: true
```

Without `include_draft_slices`, a draft goal fails generation with a clear message. With it:

- The seed covers every slice up to the goal (`slices` in `slice-2.md`), with its items: Flight, Double Jump, about
  one Abyss Artifact per 8 checks, the three permanent stat upgrades every third chapter, Copper and filler.
- The client reports draft checks and the draft goal from the save, and delivers draft items (Double Jump, the stat
  upgrades) like the verified ones, live and in the save.
- Install the `story-grants-removed` override, so the story doesn't hand out Flight and Double Jump itself.

## Verifying as you play

Every draft check the client sees completing goes into `crimsonipelago_data/logs/live-YYYYMMDD.jsonl`:

```json
{"event": "draft_check", "name": "For Honor: step 1", "ap_id": 14294964256, "region": "Hernand in Chaos", ...}
{"event": "draft_goal", "goal": "chapter_2", ...}
```

The client window shows them as "Completed check: … (draft: now seen in play)". After a playthrough, the logs say
which checks fired; those are marked verified (`"verified": true`, evidence: the log). A slice whose checks have all
fired, and whose goal was reached, becomes verified, and its goal no longer needs the option.

Things to note while playing, for the logic:

- **A check that never fires:** an objective the story skipped, or one that isn't what the data suggests. It's
  removed or fixed.
- **Stuck without an item:** the story needed an ability before the logic gave it. Note where.
- **Never needed an item where the logic said so:** Double Jump in *Hernand in Chaos*, for example. The gate
  can be relaxed.

## Optional categories: regional quests and Abyss ruins

`tools/draft_extras.py` adds two categories, off by default (`included_check_categories`):

| Category | Checks | Where |
|---|---|---|
| `regional` | 200: every objective of the 35 regional quests (the four Witches and their sanctums, *Legendary Wolf*, *Emperor of the Bonepit*…) | "Regional quests: Hernand", "…: Calphade", … |
| `abyss_ruins` | 547: one per ruin (100 per region, 47 Abyss islands) | "Abyss ruins: Hernand", … (islands need Flight) |

A region's checks open with the chapter the story first goes there: Hernand and the Abyss islands 2, Calphade 6,
Pailune and Beighen 7, Demeniss 8, the Crimson Desert and the Wayward Woods 9, Delesyia 10. That's a guess, so these
checks **only ever hold filler** (`no_progression`, Archipelago's "excluded" locations): a check that opens later or
earlier than the guess can't hold anything another player waits for.

## What the drafts don't have yet

- **Readable names:** checks are "Mission: step N". The quest log's lines come from the save editor's
  `mission_names.json`: `python -m crimsonipelago.tools.draft_slices story.json --chapters 3-13 --names
  mission_names.json` on a fresh catalogue (and the same for slice 2).
- **Three missions** the 22 Oct tables don't have (*Return Home*, *Broken Claws*, *Lust for Power*): a fresh
  extract adds them. Chapter 9's goal is *New Perspectives* until then.
- **Later abilities** (Back Hang, Aerial Maneuver, Focused Force Palm, Call Dragon) stay with the story: no override
  for them yet, so no gates past Double Jump.
- **Discoveries, treasure** (Abyss gates, Sealed Abyss Artifacts): not yet.
