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
- Install the override the client names when it connects, so the story doesn't hand out the abilities the seed
  does (`story-abilities-removed` for whole-game seeds from 26 Oct, `story-grants-removed` for earlier ones).

## Verifying as you play

Every draft check the client sees completing goes into `crimsonipelago_data/logs/live-YYYYMMDD.jsonl`:

```json
{"event": "draft_check", "name": "For Honor: Go to Hernand Square", "ap_id": 14294964256, "region": "Hernand in Chaos", ...}
{"event": "draft_goal", "goal": "chapter_2", ...}
```

The client window shows them as "Completed check: … (draft: now seen in play)". After a playthrough, the logs say
which checks fired; those are marked verified (`"verified": true`, evidence: the log). A slice whose checks have all
fired, and whose goal was reached, becomes verified, and its goal no longer needs the option.

```powershell
py -m crimsonipelago.tools.verify_drafts logs\live-20261026.jsonl logs\live-20261027.jsonl            # report
py -m crimsonipelago.tools.verify_drafts crimsonipelago_data\logs --write                              # all logs, and mark
```

The report lists per chapter how many draft checks were seen, story checks of a finished chapter that never fired,
draft items the game accepted (Double Jump, the Abyss stat items: confirmed by hand once they did what they should),
and checks done before an item their logic waits for had arrived (where a gate can be relaxed). `--write` marks
checks and goals verified and records the new seed fingerprints, so the playthrough that produced the logs keeps
going on the updated client (*Updating while a seed runs*, below). Seeds without draft slices leave unverified
optional checks (regional quests, ruins) out, so a chapter's goal is verified once its story checks are.

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

- **Readable names** for 9 story checks: the save editor's `mission_names.json` has no text for them, so they're
  still "Mission: step N". Every other draft check carries its quest log line
  (`python -m crimsonipelago.tools.name_checks mission_names.json`, 26 Oct).
- **Three missions** the 22 Oct tables don't have (*Return Home*, *Broken Claws*, *Lust for Power*): a fresh
  extract adds them. Chapter 9's goal is *New Perspectives* until then.
- **Later abilities** (Focused Force Palm, Back Hang, Aerial Maneuver, Call Dragon) are items and gates since
  26 Oct (`story-abilities.md`); seeds from before keep them with the story.
- **Discoveries, treasure** (Abyss gates, Sealed Abyss Artifacts): not yet.

## Updating while a seed runs

A seed carries the fingerprint of the catalogue it was made from (its *scope* fingerprint: everything the seed is
made of, names and logic included). Each catalogue's fingerprints are recorded in `data/seed_fingerprints.json`,
with what its seeds play by: every check's detection and every item's delivery, as a short hash each.

A client with a newer catalogue looks the seed up there and accepts it when **every check and item the seed was
made with still exists**. So these updates are safe in the middle of a playthrough:

- confirming draft checks, goals and items (`verified`, evidence), and readable names;
- relaxing or tightening logic (it only matters when a seed is generated);
- adding checks, items or whole categories: they aren't in the running seed (the server's list of its checks
  decides), and new seeds get them;
- fixing how a check is detected or an item delivered: ids are never reused, so a changed entry is a fix, and the
  running seed uses it (the client says how many changed).

Removing a check or item the seed holds isn't safe: the client refuses the seed, naming what's gone. A check that
turns out to be wrong is fixed in place, not removed, while someone plays it.

After any change to the catalogue data, run `python -m crimsonipelago.tools.record_fingerprints`. A test fails
until it's done.
