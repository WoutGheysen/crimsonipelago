# Slice 2: chapter 2, *Golden Greed* (draft)

- **Status:** draft in the catalogue (25 Oct 2026). Every check and the goal are **unverified**, so the goal isn't in
  the player options yet, and first-slice seeds don't include any of it (slices, below).
- **Built from:** the game's own tables (`crimson-data story --plan main_story.json`: missions, objectives in order,
  rewards), not yet from saves. The first slice's checks were confirmed in saves; this one gets the same treatment
  in its playthrough.

## Slices in the world

The world now grows a slice at a time:

- **Regions** carry a slice number, and so do **goals** and **items** (default 1).
- A seed covers the slices up to its goal's: their regions, checks, rules and items. So a later slice can sit in the
  catalogue as a draft without changing earlier seeds.
- The **seed fingerprint** (slot data `catalogue_fingerprint`) now covers only what the seed is made of
  (`Catalogue.scope_fingerprint`). A client with a newer catalogue still accepts a first-slice seed, as long as the
  first slice itself didn't change. Seeds from before this change carry the old whole-catalogue fingerprint: they
  need regenerating once.
- **Validation:** a verified goal can't cover an unverified check.

## The draft: 50 checks in 4 regions

| Region | Checks | Needs | From |
|---|---|---|---|
| After the Jump | 2: *Woman in White* steps 3–4 (*Return to Hernand*, *Chapter Transition*) | **Flight** (the scripted glide) | first slice's checks 43–44 |
| Unexpected Gift | 11: *Where the Light Leads*, *Memory Fragment*, *Reunion* | — | quest 1000004 |
| Hernand in Chaos | 28: *For Honor* to *Secrets Hidden in the Dark* | **Double Jump** | quest 10011000 |
| The End of Greed | 9: *The Dark Veil* to *Cheers Echoing From the Edge* | — (after Hernand in Chaos) | quest 1000267 |

- Checks are the objectives, named "Mission: step N" for now. The readable names (the quest log's lines) come from
  the save editor's `mission_names.json` in one pass.
- Detection: `mission_state` of the objective (state 5), or its mission's completion (`parent_mission_key`), exactly
  as in the first slice. Location ids: 10,000,000,000 + the objective's key.
- **Goal `chapter_2`:** *Cheers Echoing From the Edge* (mission 1000193) completed, the end of chapter 2.

### Logic

The story is linear, as in chapter 1. The rule from the progression draft: the story would teach an ability at some
point, so everything after that point might expect it, and logic requires the item from there on.

- **Flight** for everything after the woman in white (the glide that killed Kliff without it in E2).
- **Double Jump** for everything after *Reunion*, whose last objective teaches it (`Mission_UnexpectedGift_Liftable_4`).

This is stricter than the game may need, which is safe: it can only make a seed wait longer for an item, never ask
for something that can't be had. The playthrough relaxes it where Double Jump turns out not to matter.

Not randomized in this slice (the story keeps handing them out): Lift and Detect (*Reunion*), Double Kick and the
Abyss teleport scroll (*Cheers Echoing…*), and the gear and inventory expansions the objectives reward.

## Items

| Item | id | Delivery | State |
|---|---|---|---|
| Double Jump | 1111 | knowledge 1001116, live like Flight | **needs a live test** (`plugin give-knowledge --key 1001116` on a save without it) |
| Flight, Abyss Artifact, 100 Copper | | as in the first slice | verified |

## The override: `story-grants-removed`

Like Flight, Double Jump would come from the story anyway. `story-grants-removed` is one override doing both:

- *Speak with the woman in white* teaches the Hedgehog codex entry instead of Flight (as `flight-grant-removed`);
- the end of *Reunion* teaches **Alfred of Hernand**'s codex entry (`Knowledge_Her_Alfred`, 1001105) instead of
  Double Jump (`Knowledge_DoubleJump`, 1001116). Same length, so the record keeps its size: drop set 1016274, 2 values
  and 2 names, checked against the tables extracted on 22 Oct.

Only one override can be installed at a time; this one includes everything `flight-grant-removed` does. The client
accepts either for Flight, and asks for `story-grants-removed` once Double Jump is a real item.

## To make it real

1. **Live test of Double Jump** (5 min): on a save before chapter 2, or one where it hasn't been learned,
   `plugin give-knowledge --key 1001116`, then jump twice.
2. **The override in the game:** `crimson-mod check story-grants-removed`, then install it (game closed), and check
   that *Reunion* no longer teaches Double Jump.
3. **Readable names:** `mission_names.json` from the save editor folder.
4. **The chapter 2 playthrough** with saves, as for the first slice: each check seen completing, and where Double
   Jump is really needed.

Then the checks and the goal are marked verified, the goal joins the options, and Double Jump becomes supported.
