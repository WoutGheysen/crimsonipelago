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

## Third run: calls of the transaction functions (2026-10-24, plugin 0.6.0-r2c)

Execute watches on add (`+0x277e080`), commit (`+0x2780570`), commit-add (`+0x27aed40`) and commit-remove
(`+0x27b0b70`) while looting four times, buying and selling. No stutter, no crash.

- **Everything runs on one game-logic thread**, and it uses **one transaction object** for all of them
  (the same address in every call): get, fill, commit, reuse.
- **Something commits a transaction all the time**, in pairs (callers `+0x2b753f6`/`+0x2b759e4` under
  `+0x2bd0bcc` and `+0x2bd064b`), with or without the player doing anything. Those filled the 8-records
  quota per function, crowding out most of the looting. Plugin 0.6.1 keeps the quota per caller instead.
- **Looting an item goes through the handler seen before** (`+0x2b4bc8e` → commit `+0x2b4bd92`): the
  unexplained +22 copper of the second run was a loot.
- **The shop goes through a wrapper** that adds and commits in one go (`+0x2781588` add, `+0x278164d`
  commit), called for buying (`+0x2b38b41`, `+0x2781852` ← `+0x286e82e`) and selling (`+0x2c059bd`).
- **What "add" receives** (loot): the transaction, a result, the same owner object every time (r8), and in
  r9 a small item description: a pointer, two 4-byte 1s, then a 4-byte item key (0x7BD here; the Copper
  stack's record has 0x7BC at the same place), followed by more arguments pointing at small structures and
  flags, among them the reason 0x1C.

## Fourth run: looting items, a copper pouch, buying (2026-10-24, plugin 0.6.1-r2c)

With a quota per caller, every action showed its own callers of "add" (`+0x277e080`), each with its own
reason code (the low word of the 13th argument): picking up items `0x0B` (`+0x2b83513`), opening the
pouch `0x1C` (`+0x2b4bc8e`), looting `0x20`/`0x0E`/`0x16`/`0x23` (several handlers), buying `0x3F`
(`+0x2b38b41`) and `0x02` (`+0x2c00f17`).

From the code (second dump, 58 functions):

- **`+0x2781490` is "add, check and commit" in one call**, with the same arguments as add; most game
  code uses it (directly or through the smaller `+0x2781780`, which fills in defaults). After the commit it
  tidies up the transaction (`+0x2780820`, `+0x2781280`, `+0x2781320`).
- **Add's arguments:** rcx the transaction, rdx the result, r8 the owner (the same object every time:
  stored at the transaction's start), r9 a 12-byte identifier (copied), and **the 6th argument a list:
  `{pointer, count}` of 0x38-byte entries**, one per change, each checked by `+0x22ce360`. That list is
  where the item and amount must be. The 10th argument goes to transaction +0x20, the 18th (a byte) to +0x28.
- The entries themselves live on the heap, out of reach of a one-level snapshot: plugin 0.7.0 follows a
  pointer one level further (`follow`), and `targets.json` now asks for those entries.

## Fifth run: the change entries (2026-10-24, plugin 0.7.0-r2d)

Looting an item, picking up a pouch, opening it and buying, with the entry lists followed. Together with
the entry checker `+0x22ce360` from the third code dump:

- **An entry is a variant: its first byte is the kind of change (0 to 0x11, 18 kinds),** dispatched
  through a jump table. The checker sorts entries into per-kind lists in a context that points into the
  transaction (+0x110, +0x120, +0x130, +0x178, +0x198).
- **Kind 0 adds an item: the entry holds, at +0x30, a pointer to an item record**, the same 0xC8-byte
  layout as an inventory stack (item number at +0x00, the item's key handle at +0x08, a 16-bit index that
  is 0x7BC for Copper, count at +0x10). The checker merges it into an existing stack with the same item
  (through the count change seen in the first run) or copies it into a new stack.
- **Kind 1 takes a 16-bit key directly at +0x08** and makes a 0x58-byte record for another subsystem
  (to identify: possibly knowledge, which would cover Flight).
- Buying showed entries with 4-byte values around one million (0x0F4A1C, 0x0F45A6, …) next to a count of 1:
  likely item keys of what was bought or paid; to confirm against the save's item keys.

## R3 plan

Give 100 Copper live, in the game-logic thread, the way looting does:

1. Find the Copper stack record in memory (our item number 900000002 marks it) and copy it as the item:
   new item number, count 100.
2. Make one kind-0 entry pointing at it, and a `{pointer, 1}` list.
3. Call add-check-commit (`+0x2781490`) with the transaction and owner the game itself uses (the same objects
   in every call: taken from a watched call), the other arguments copied from a looting call, and our list.
4. Run it on the game-logic thread: an execute watch on a function that thread runs every frame, which
   calls ours from the handler, once.

Still to work out: the remaining arguments' meaning (reason code: loot 0x0B/0x1C), and how the key handle
maps to the item keys in the save (to give items other than Copper).

### As built (plugin 0.8.0-r3, `plugin give-copper`)

- **The call:** `+0x2781780`, the small add-check-commit the shop uses (`+0x2c00f17`), which fills in the
  rarely used arguments itself. The shop's call, read from its code: `(transaction, result, owner,
  removals, &additions, 1, unix time, game clock, reason 2, 0, -, 0xFFFF, 1, 1)`, with transaction +0x1AB
  set to 0 just before. R3 passes the same with reason 0x0B (picking up), an empty removals list
  `{0, 0}` and additions `{&entry, 1}`.
- **Transaction, owner, thread:** taken from the next "add" the game makes by itself (it does every few
  seconds); the game-logic thread in all recordings went `+0x277331e` → `+0x2774a0d` → **`+0x2774b20`**
  (runs one task) → the task. At `+0x2774b20`'s entry no task is in progress, so the plugin makes its call
  there, from an execute watch on that thread only.
- **Unix time:** the 10th argument of every recorded add was the current Unix time (0x6ADC053D = 01:09 UTC,
  the time of that test); the game clock (11th) is taken from the call that was just seen.
- **The item:** a copy of the game-logic side's Copper stack record (`+0x0C` = 0xFFFFFFFF), found through
  our item number, with count 100 and a new item number from 905,000,000. The checker copies it into the
  transaction (stackable items merge), so the game never keeps a pointer to our copy.

### Result: R3 worked (2026-10-24, 20:39)

`plugin give-copper` on the first seed's playthrough, game 2.03.02:

- the transaction, owner and game-logic thread came from the game's own next "add" (a background task,
  `+0x2b753f6`);
- the plugin made the call 78 ms after queueing it, on that thread, from the task runner; result code 0;
- **the player saw it straight away: the bag, the money at the top right, and the game's own message**
  for receiving it. Everything the game normally does for a pickup happened, because it was the game's
  own code doing it;
- **saved, quit to the main menu, loaded: the money was still there.**

The Copper record the tool read back right after was the screen side's copy (+0x0C = 0), which had not
caught up yet (468 before and after): the game-logic side's copy wasn't among the records next to our item
number this session. Harmless here; the bag and the total showed the new amount.

## Earlier: next after the fourth run

1. `plugin watch-calls` with 0.7.0: the change entries of loot, pouch and purchase; `plugin dump-code`
   adds the entry checker and the transaction helpers (`dump_extra` in `targets.json`).
2. R3: build such an entry for an Archipelago item and call `+0x2781490` on the game-logic thread.

## Plan after the third run

1. `plugin watch-calls` again with 0.6.1 (quota per caller), then `plugin dump-code` on that report: the
   code of the add function and its callers shows what each argument means.
2. R3: make that call ourselves, on the game-logic thread.

## Earlier plan

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
