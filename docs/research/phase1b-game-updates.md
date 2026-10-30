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

## First check on the real game (25 Oct, game 2.03.02)

`r7-signatures-20261025-114527.json`: the whole executable (347 MB of code) searched in **9.4 s**.

- `probe`, `give`, `runner`, `knowledge_table`: each found once, where expected.
- `item_table`: found **121 times**. The item lookup (+0x38ab60) reads its table with the same instructions that
  120 other functions use for their own tables.

Two changes:

- **`via_call` signatures.** The item table is now found by following a call to the item lookup from
  "commit: add items" (+0x27aef0b), which calls it only for items. Then the instruction reading the table is found
  in the called function.
- **Agreeing matches.** A global whose pattern matches several times is accepted when every match leads to the
  same address. A function must still match exactly once.

## The "not now" code

`plugin error-sites --code 0x40128a9b` (r5-errors-20261025-114611) found no place in the dumped code that loads
that code from a global, so it's produced in code not dumped yet. The way to find it: a write watch on the call's
result field during a refused call. The first write of `0x40128a9b` there names the code that decided. Not needed
for delivery (the client waits and retries), only to make big batches faster.

## The first real update (30 Oct): 1.0.0.3424, then 1.0.0.3446

Two patches in one evening (build stamps 1793296370, then 1793355216). The signature search ran by itself on the
first one and found nothing for `probe`, `give` and `item_table`, so live delivery stayed off and the 70 items
waiting stayed queued, as designed. Nothing was lost or given twice.

What had changed, from `plugin find-moved` (each 8-byte piece of the old pattern votes for where the function went)
and `plugin peek` (the bytes of one function):

| Target | 1.0.0.2976 | 1.0.0.3446 | What changed |
|---|---|---|---|
| probe (transaction: add) | 0x277e080 | 0x29aeac0 | one register: `mov rsi, rcx` where it was `mov r14, rcx` |
| give (add and commit) | 0x2781780 | 0x29b24c0 | saves rsi too, so everything after the prologue moved 4 bytes; same frame and locals |
| runner | 0x2774b20 | 0x29a53e0 | nothing: the old signature matched |
| knowledge_table | 0x6d69a98 | 0x71b29b0 | nothing: the old signature matched |
| item_table | 0x6d69aa8 | 0x71b29c0 | the lookup (+0x3dcb40) now takes the key in `edx` (`mov edi, edx; mov rbx, [table]; cmp edx, [rbx+8]`) instead of reading it from `[rcx]` |

The two tables are still 16 bytes apart, and the item table's layout is the same (count at +8, data at +0x58).
The near-match search put `give` 4 bytes late, where the anchors after the inserted instruction pointed; the real
start is the `mov r11, rsp` after the padding.

The new patterns replace the old ones (Steam doesn't keep old builds), and build 1793355216 is listed in
`builds`, so the client doesn't need to search. Save writes stay off for the new Steam build until a save written by
it has been checked; live delivery covers the items in the meantime.
