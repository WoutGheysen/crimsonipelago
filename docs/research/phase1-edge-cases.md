# Phase 1: live delivery around menus, loading and dying

Live delivery works while you stand in the open world (R3–R5). Phase 1 makes it hold up through a whole
playthrough. This note covers the cases most likely to break it, the test that checks them, and the log the client
keeps while you play.

## The risk: an item given live, then lost to a load

An item given live exists only in the running game until the game saves. The client marks it delivered (`COMPLETE`)
once a save is written after it, and gives it again if the game closes without saving.

One case slips through. Suppose you load a save from the menu, or dying puts you back at an earlier state, before the
game has saved. The item is gone. The game then autosaves a little later, and the client takes that save as proof
the item was kept.

- **Knowledge (Flight and the like):** already covered. Once a save is written, `reconcile` sees it's missing and
  gives it again.
- **Stackable items:** not covered. A count in the save can't tell "spent" from "lost".

The fix is to notice a load from the plugin's side. Items still `IN_GAME` at that moment were never saved, so they go
back to `PENDING`. A load means anything loaded predates them, whichever save it was.

The candidate signals were the transaction the game's own "add" uses, its owner, the game-logic thread, and the
game's clock (an argument of every add). The edge-case test recorded all four around a load and a death: see below.

## What the test found (game 2.03.02, 25 Oct 2026)

`r6-edges-20261025-000736.json`, on the first seed's playthrough:

| Step | Silver given just before | Transaction | Delivery after |
|---|---|---|---|
| Map open | - | no use of "add" while the game is paused (also when alt-tabbed) | waits until you're back in the game |
| Horse | - | unchanged | at once |
| Fast travel | - | unchanged | at once |
| Load the manual save | **lost** | owner `…0200` → `…0400`, clock 459.0M → 448.9M | 55 s |
| Die | kept: no reload, you get up where you fell | unchanged, clock goes on | 33 s |
| Main menu, load again | **lost** | owner → `…3e20`, clock back again | 24 s |

- The transaction's address and the game-logic thread stayed the same all session, across both loads.
- **A load changes the owner and sets the clock back.** Nothing else did either. The clock is the game's own time,
  an argument of every "add", and only goes forward in play.
- The slow deliveries after a load or a death came from the client: it dropped a transaction it had last seen more
  than 20 s ago, then waited for the game's next "add", which can be 80 s away. The address doesn't change, and
  `_fresh_probe` already checks that the transaction's first field is still the owner (it isn't after a load). So
  the client now keeps a sighting for 5 minutes.

## The guard (client)

- `LiveChannel` counts a load whenever the owner changes or the clock goes back. A 32-bit clock wrapping round
  doesn't count. Each load is logged as `{"event": "load"}`.
- `DeliveryEngine.notice_loads`: on a new load, items still `IN_GAME` that were given before the load was noticed
  go back to `PENDING`, and are given again into the loaded game.
- `settle_in_game`: while the game runs, an item is `COMPLETE` only once there's a save after it **and** the game
  has been seen running after that save. An autosave right after a load doesn't count until the next sighting
  shows whether a load came first. With the game closed, a save after the item is enough, as before.
- Left open: loading a save older than one that already held the item (an older manual save). Knowledge is
  covered by `reconcile`; a stack would stay lost. That's rare, and the save can't tell spent from lost.

## The test: `plugin edge-cases`

On a test playthrough (not a seed you're playing), with the game running and the plugin loaded:

```powershell
py -m crimsonipelago.client.main plugin edge-cases
```

It asks you to make a manual save. It then goes through seven steps:

1. Standing
2. Map open
3. On the horse
4. Fast travel
5. Loading the manual save
6. Dying
7. Quitting to the main menu and loading again

Around each step it gives 1 silver and asks what happened. Steps 5–7 give 1 silver before the step as well, to see
whether it survives it. The report is `research/r6-edges-*.json`. For each step it holds every use of the game's
"add" seen, a summary (which transactions, owners, threads and callers, whether the clock went back, the longest
gap) and how long delivery took afterwards.

## The playthrough log

`connect` now writes `logs/live-YYYYMMDD.jsonl` in the client's data folder, one line per event:

- `connected` / `disconnected`: plugin version, game build.
- `give`: what was given, the outcome, the game's result code, how long the game took. A refusal also stores the
  transaction's first 0x1C0 bytes just before the call (`tx_before`), so refused and accepted Flight attempts can be
  compared.
- `transaction`: the first one seen, and each time its address, owner or thread changes, or the clock goes back.

This log is the record of the Phase 1 fresh-seed playthrough. It also answers the open question from R5: the Flight
refusals (code `0x78c0a4e9`, from the checker at `+0x22ce570`) came before the client recorded the transaction's
state.

## The live log of 25 Oct: the game says "not now" after a run of items

`live-20261025.jsonl`, two client sessions on the first seed's playthrough:

- 00:59:41: the game took 8 in a row (an Abyss Artifact, 6 × 100 Copper, Flight), each about 0.3 s apart. The 9th
  (Copper) was refused with code `0x40128a9b`, and an Abyss Artifact 0.4 s later the same way.
- 01:18:48: it took 11 in a row (Copper and Abyss Artifacts). The 12th was refused with the same code.
- Before the refusals, the transaction (first 0x1C0 bytes) looked the same as when items are accepted. One
  difference: two small lists (+0xC8 and +0x1B0, `{pointer, 0, capacity 1}`) were allocated, where the accepted
  R5 sample had them empty. Not enough yet to say what fills up.
- The client then wrote Copper and the Abyss Artifact off for the rest of the session. That rule was meant for gear,
  which the game never takes. Copper sent later waited for the save, without a word in the client window.
- The log wrote the same "refused before" and "waiting" lines on every tick: 7,342 lines.

Changes:

- After any refusal, the client waits until the game has made an add of its own before trying anything again.
- An item the game took earlier in the session, or any refusal with code `0x40128a9b`, counts as "not now". It's
  retried up to 5 times before it's left for the save.
- Refusals show in the client window. Repeated waits are logged once. Each of the game's own adds is logged
  (`"seen"`), so the next log shows whether the wait was enough.
- Still open: what the game counts. `plugin error-sites --code 0x40128a9b` finds where the game produces that code.
