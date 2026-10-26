# The later story abilities as items (draft, 26 Oct 2026)

After Double Jump, the story teaches four more movement abilities. Now they come from Archipelago too, which gives
the second half of a whole-game seed something to wait for.

| Ability | Item id | Knowledge | Story step that teaches it | Drop set |
|---|---|---|---|---|
| Focused Force Palm | 1112 | `Knowledge_JiJeongTa_Deep` 1001756 | *An Unknown Voice: Find the trials* (chapter 9) | 1014747 |
| Back Hang | 1113 | `Knowledge_CharacterClimb` 1003571 | *Mark of the Scar: Face the guardian deity of Urdavah* (9) | 1014748 |
| Aerial Maneuver | 1114 | `Knowledge_Rope_AirPull` 1003512 | the same step | 1014749 |
| Call Dragon | 1115 | `Knowledge_CallDragon` 1000175 | *Whispers in the Wind: Go to the Nest of Valor* (11) | 1014833 |

Found with `story.attach_rewards` on the tables extracted on 22 Oct. Each drop set is referenced by exactly one
mission record, and nowhere else.

## The override: `story-abilities-removed`

It does everything `story-grants-removed` does, and the same for these four. Each drop set teaches a harmless
codex entry of the same name length, so the record keeps its size:

| Drop set | Was | Now |
|---|---|---|
| 1014747 | `Knowledge_JiJeongTa_Deep` | `Knowledge_Domestic_Sheep` (1009, the Sheep) |
| 1014748 | `Knowledge_CharacterClimb` | `Knowledge_Sockeye_Salmon` (2017) |
| 1014749 | `Knowledge_Rope_AirPull` | `Knowledge_Domestic_Pig` (1070, the Pig) |
| 1014833 | `Knowledge_CallDragon` | `Knowledge_Desert_Fox` (1131) |

Checked against the 22 Oct tables: every patch replaces 2 values and 2 names, and the table keeps its size.

## Which override for which seed

A seed's slot data now lists the abilities it hands out instead of the story (`story_items`). When the client
connects, it checks that the installed override takes exactly those out of the story, and names the right one if
not:

- **Too small:** the story still teaches an ability the seed is meant to give.
- **Too big:** the story no longer teaches an ability the seed never hands out, so the player would never get it.
  The client warns, unless that ability comes after the seed's last chapter.

Seeds from before 26 Oct don't carry `story_items`. For them it's Flight, plus Double Jump with draft chapters, so
they keep `story-grants-removed`. **The seed Wout started on 25 Oct must not get the new override:** that seed
doesn't hand out Back Hang or the others, so the story has to.

## Logic

As for Double Jump, everything after the step that teaches an ability needs it. This is stricter than the game may
need, which is safe:

- the step after *Find the trials* (*Cloister of Enlightenment*) and every later region need Focused Force Palm;
- *Shackles of Fate* and everything after need Back Hang and Aerial Maneuver;
- the Flying Fortress (*Flying Fortress Orbian*, both steps) and everything after need Call Dragon.

On 30 whole-game seeds the four land anywhere from chapter 1 to the chapter before their gate. The medians are
checks 137, 174, 161 and 318 of the story.

## To make it real

1. A seed with them, the override installed, and the playthrough of chapters 9 and 11. The live log shows each
   delivery, and the player says whether the ability works.
2. Where they're really needed. `verify_drafts` lists checks done before an item arrived, which is where a gate
   can be relaxed.
