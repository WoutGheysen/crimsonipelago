# R2: the code that changes your money

- **Game build:** 1.0.0.2976 (build stamp 1790086912), shown in the game as 2.03.02
- **Plugin:** 0.4.0-r2, `plugin watch-money`
- **Addresses** are offsets in `CrimsonDesert.exe` (`+0x…`); they hold until the game updates.

## First run (2026-10-24)

Money 1.15 → 1.00 (bought something) → 1.38 (sold something), with watches on the two item records next to
our item number 900000002 (the Copper stack the client added) and two other places. Denuvo didn't mind the
watches: 91 game threads armed, no stutter, no crash.

**Every write came from the same instruction**, `+0xf4a0480`, in the function starting at `+0xf4a03c0`:

```
add  rdx, r8                 ; new count = old count + change
mov  qword ptr [rbx+0x10], rdx   ; the stack count, 16 bytes after the item number
mov  rax, r11
mov  dword ptr [r11], 0      ; result code: 0 = done
```

So that function changes a stack's count by an amount and reports a result code: a general "change stack
count" step, not specific to money.

**Each change wrote both records, from two different threads:**

| | thread A (6872) | thread B (24268) |
|---|---|---|
| caller of the count change, buying (count goes down) | `+0xe442150` | `+0xe442150` |
| caller of the count change, selling (count goes up) | `+0xf48c907` | `+0xf48c907` |
| next up, buying / selling | `+0x27b0c4b` / `+0x27af1e5` | `+0x9c5337` / `+0x9c49b9` |
| shared above that | `+0x27805ad`/`+0x27805da` ← `+0x278164d` ← `+0x2781852` | `+0xbc0ece`/`+0xbc0730` ← `+0x35d4f5` ← `+0x35d1bb` |
| then | buying `+0x2c00f17` ← `+0x2a071a2`; selling `+0x2c059bd` ← `+0x2c44fc0` ← `+0x2a07ea9` | `+0xad246f` ← `+0x6173f2` ← … |
| thread loop | `+0x2774b44` ← `+0x2774a0d` ← `+0x277331e` | `+0x3f412e6` ← … ← `+0x4895c3e` |

Reading:

- The game keeps **two copies of the inventory**, each changed by its own thread. This matches Pearl Abyss's
  engine heritage (Black Desert): a game-logic side that decides (thread A: separate handlers for buying,
  `+0x2c00f17`, and selling, `+0x2c059bd`, under one dispatcher, `+0x2a07…`) and a side that mirrors the
  result for the screen (thread B). Which copy the save comes from is still open; the money total at the
  top right is updated separately.
- On thread A, buying and selling meet at `+0x278164d` / `+0x2781852`, which then picks "remove" or "add"
  (`+0x27805ad` / `+0x27805da`): a strong candidate for the game's "change the inventory" transaction.
  If looting and quest rewards also pass through it, that's the place to give items from.

## Second run (2026-10-24, plugin 0.5.0-r2b), and the code

4.18 → 4.40 (+22; what the player did wasn't labelled yet). The game-logic thread took a third path into
the same place:

```
count change (+0xf4a03c0) <- +0xf48c907 <- +0x27af1e5 <- +0x27805da <- +0x2b4bd92 <- +0x2b4b7df <- +0x2b49ef8 <- +0x2a04f0d
```

So selling and this event meet one level lower than buying and selling did: at **`+0x2780570`**, not at
`+0x278164d` (that one belongs to the shop). The other watched places were the screen side: `+0x644970` and
`+0x644286` on the mirroring thread copy the new amount into what's displayed.

`plugin dump-code` saved the 54 functions on these paths. What they show (offsets only; the code itself
stays out of the repository):

- **`+0xf4a03c0`** changes a stack by an amount: `(stack, result, change)`, with limits checked against
  the maximum; result code 0 means done. The stack record is 0xC8 bytes: item number at +0x00, a 4-byte
  key at +0x08, count at +0x10, a weighted value at +0x18 (recomputed when a stack grows), a time at +0x98.
- **`+0x2780570` commits an inventory transaction**: `(transaction, result)`. It applies, in order, the
  removals (transaction +0x38 → `+0x27b0b70`, buying's path), the additions (+0x30 → `+0x27aed40`, selling's
  and the new event's path), and several more parts (+0x150, +0x40, a list at +0xC8, +0x140 … +0x188).
  So one transaction can carry many kinds of change, not only items.
- **The event's handler (`+0x2b4baf0`) shows how a transaction is used:** get the game-logic thread's
  transaction (`+0x2bbab00`, kept per thread), set two reason bytes (+0x1AB = 9, +0x1AC = 2), add to it
  with **`+0x277e080`** (a dozen arguments: what, how many, flags), then `+0x277fe00`, `+0x277ffb0`,
  `+0x2781170`, `+0x2780450` (checks and preparation) and finally the commit, `+0x2780570`.

That's the pattern to copy for Archipelago items: get the transaction on the game-logic thread, add the item,
check, commit. The game then mirrors it to the screen side (the total at the top right, the popup) as it
does for loot.

## Next

1. `plugin watch-calls` (plugin 0.6.0): execute watches on `+0x277e080` (add), `+0x2780570` (commit),
   `+0x27aed40` (commit: add items) and `+0x27b0b70` (commit: remove items), recording the arguments and the
   memory behind them while the player loots, buys, sells and finishes something, each labelled. That
   shows what the "add" call expects for a given item and count.
2. R3: make that call ourselves, on the game-logic thread.

## Earlier next steps

1. Watch again while looting coins and finishing something that pays money, labelling each action
   (`plugin watch-money` now asks what you did): do those also pass `+0x278164d`?
2. `plugin dump-code` saves the code of each function on these paths (not committed: it's the game's
   code) so the transaction's inputs can be worked out.
