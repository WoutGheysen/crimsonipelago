# How the game stores items (and how the client adds them)

- **Status:** works in the game (test F passed, 2026-10-23); Abyss Artifacts and Copper are Archipelago items
- **Build:** 25477059
- **Inputs:** the 30-save series of the new campaign and the old campaign's saves

## What the saves show

Items live in `InventorySaveData._inventorylist[]` (one entry per bag) `._itemList[]`. Bag 2 is the main
bag; bag 5 holds quest items (letters, keys). Equipment worn is elsewhere (`EquipmentSaveData`).

| Field | What it is |
|---|---|
| `_itemNo` | unique number per stack, from a counter: 80–89 in the opening, then 1,000,000 onward. The last number handed out is also stored as `_generateNo` in `lobby.save`. |
| `_itemKey` | which item (1 = Copper, 75001 = Abyss Artifact, …) |
| `_slotNo` | position in the bag. A new stack takes the **lowest free slot**: the broom took slot 1 after the Bekker Axe left it. |
| `_stackCount` | how many. Picking up more of something you have raises the count; no new stack. |
| `_timeWhenPushItem` | when the stack was received, **Unix seconds** |
| `_isNewMark` | the "new" marker in the inventory |

Item records come in a few layouts, told apart by which fields are present. The **misc** layout covers money,
Abyss Artifacts, keys, candles, tools: every misc record in every save is byte-for-byte identical except for
the number, key, slot, count, the time received and a second copy of the item key after the (always empty)
socket list. Food has a different layout (with cooldown fields) and is not handled yet.

The decoder misreads the fields after the socket list (it is two bytes off), so the client finds those by
structure instead: the socket list, `01 01`, the item key, charge fields, the time, the "new" mark, the
trailing size.

Bag size grows with bag upgrades (`_varyExpandSlotCount`: 3, 6, 9 after each); the base size is not in the
save. What the game does with a stack in a slot beyond the bag size is untested.

## How the client adds an item (`save_edit.give_items`)

1. If the main bag has a stack of the item: raise its count (same-size change, as in test A). A gift that
   would pass the item's stack limit is refused and waits ("the stack is full"), rather than split.
2. Otherwise: copy a misc record from the bag and set the number, key, slot (lowest free), count, the
   second key, the time (now) and the "new" mark. It goes in with the same pointer and size bookkeeping as
   Flight.
3. Item numbers start at **900,000,001**, far above the game's own counter (1,000,034 after two hours), so
   they can never collide with numbers the game hands out, whichever counter it keeps.
4. The result is decoded again: the item totals must be exactly the old ones plus the gifts, and nothing
   else may change.

When a delivery has several items and one doesn't fit, the others are still delivered; that one stays
pending with its reason.

Checked on all 32 distinct saves collected so far (with Flight learned in the same edit), and with a
synthetic save in the test suite.

## Test F: passed (2026-10-23)

Save 6 with Flight added as a new entry (not the swap from test D), 100 Copper and a Light Copper Pouch as
new stacks, and 2 Abyss Artifacts on the existing stack (3 → 5). In the game:

| Check | Result |
|---|---|
| Glide | yes: Flight as a new knowledge entry works too |
| 100 Copper | yes, shown as 1 silver: the game treats it as money |
| Light Copper Pouch, 5 Abyss Artifacts | yes |
| Use the pouch | yes, it pays out copper |
| Spend an artifact on a Flight upgrade | yes |
| Pick up a new item afterwards | yes, it goes in normally next to ours |
| Save, quit to desktop, restart, load | everything still there |

So **Abyss Artifact** (useful: Flight upgrades) and **100 Copper** (filler) are now Archipelago items.
