# Phase 2: live checks

Today a check is sent when the game saves: the client reads the save and finds the missions done since the last
one. Autosaves are about 15 minutes apart, so a check can take that long to reach the other players. Phase 2 sends
it the moment the mission completes.

## The question

The game changes the player's state through transactions. Each carries a list of change entries
(0x38 bytes, a kind byte at +0, then the kind's data). The checker at +0x22ce360 switches over 18 kinds. Two are
known so far:

- kind 0: an item, with the item record pointer at +0x30;
- kind 4: knowledge, `{u32 key at +8, u32 level at +0xC, u8 how learned at +0x10}`.

**If mission progress is also a kind**, the watch live delivery keeps on the game's "add" already sees every
mission as it completes. Live checks would then mean recognising those entries: no new research in the game.

## Step 1: record it during the fresh-seed playthrough (no extra session)

The client's watch on "add" follows both lists, additions (6th argument) and removals (4th), 16 entries each.
Every "seen" line in `logs/live-*.jsonl` now holds each entry's kind, its numbers at +8 and +0xC, and its raw bytes.

`python -m crimsonipelago.tools.live_log LOG...` summarises a log: the entry kinds seen, and every entry
whose numbers are the key of a check in the catalogue (all 42 are missions: 57 mission and parent-mission keys).

## Step 2: decide from the log

- **Mission keys show up in entries:** read the entry's kind and the state it sets. Send the check when a
  completing entry for a check's mission goes through. Keep the save as a second source, as now: a check missed
  live is still found at the next save.
- **They don't:** missions change outside transactions. Then it's an R1/R2-style session: find the game's mission
  table, the way the knowledge table was found (+0x6d69a98), and watch the state of the catalogue's missions.

## Care

- A sent check can't be taken back, so a live check has to be certain: an entry that completes the mission, not
  one that starts or updates it.
- Loading an older save after a check was sent is fine: Archipelago keeps checks, and the player does the mission
  again in the loaded game.
