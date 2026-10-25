# Phase 1b: surviving game updates

Live delivery calls five things in the game: the transaction "add" it watches (`probe`), the add-check-commit it
calls (`give`), the game-logic task runner it waits for (`runner`), and two table pointers (`item_table`,
`knowledge_table`). `targets.json` records where they are for each researched build (the build stamp in the
executable's header). A game update moves all of them, and until now live delivery switched off until they were
found again by hand.

## Signatures

`targets.json` now also has a **signature** for each, made by `tools/make_signatures.py` from the R2–R5 code dumps:

- **A function:** its first 56–75 bytes, whole instructions, with every byte a rebuild moves blanked (`??`):
  RIP-relative displacements and relative call/jump targets. A prologue alone is common, so each pattern is long
  enough to include the function's own first steps.
- **A global:** a few instructions around one that reads it (`mov rbx, [rip+disp]`). The address is that
  instruction's end plus its displacement, read from the running game.

Each matched exactly once in all 125 dumped functions, at the right place. Whether each is unique in the whole
executable is what `plugin find-functions` checks (below).

## In the client

On connecting to a build that `targets.json` doesn't list, the live channel:

1. Reads the PE section table, and searches each executable section through the plugin's `read` (4 KB at a time,
   searched 1 MB at a time). No plugin change was needed.
2. Requires every signature to match **exactly once**.
3. Saves what it found to `found-targets.json` in the client's data folder, so this happens once per game version,
   and logs a `signatures` event.

If anything is missing or matches twice, live delivery stays off for that version with the reason, and items wait
for the save as before. The constants that don't move (`reason`, `knowledge_live`) come from `signature_defaults`.

## What signatures don't cover

The layout of the game's structures: the transaction's flag at +0x1AB, the item table's columns, the knowledge
table's buckets. An update that changes those breaks live delivery even with every function found. The checks
already in place catch that, and live delivery stays off instead of guessing:

- the transaction's first field must be its owner;
- the item table's key column must agree with the loaded items;
- a knowledge key is looked up before it's learned;
- a fault in a call stops live delivery.

## Checking the signatures

With the game running:

```powershell
py -m crimsonipelago.client.main plugin find-functions
```

This searches like the client would and compares with what `targets.json` knows for this build. The report is
`research/r7-signatures-*.json`. "All found where expected" means the signatures are good. Anything found twice
needs a longer pattern; anything not found needs a check of the dump.
