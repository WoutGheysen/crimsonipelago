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

The candidate signals are the transaction the game's own "add" uses, its owner, the game-logic thread, and the
game's clock (an argument of every add). The edge-case test records all four around a load and a death, so we can
see which one changes. The guard goes in once the reports show a dependable signal.

## The test: `plugin edge-cases`

On a test playthrough (not a seed you're playing), with the game running and the plugin loaded:

```powershell
crimsonipelago plugin edge-cases
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
