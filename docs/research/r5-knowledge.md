# R5: learning abilities (knowledge) live

- **Game build:** 1.0.0.2976 (build stamp 1790086912)
- **Plugin:** 0.8.0-r3

## The watch (2026-10-24)

`plugin watch-calls` while learning a new skill with Abyss Artifacts, then upgrading it. Both went through the
same inventory transaction as items (add `+0x277e080`, commit `+0x2780570`), from the skill menu's handler
(`+0x2b744ed` ← `+0x2b74065` ← `+0x1082dc81`), with **one change entry of kind 4**:

| offset | learn | upgrade | what |
|---|---|---|---|
| +0x00 | 4 | 4 | kind: learn knowledge |
| +0x08 | 1005839 | 1005839 | knowledge key (u32) |
| +0x0C | 1 | 2 | level (u32) |
| +0x10 | 0x13 | 0x13 | how it was learned (u8) |

The artifacts paid for it went as removals in the same transaction.

## What the game does with it

The entry checker (`+0x22ce360`, kind 4 at `+0x22ce807`) looks the key up in the knowledge table (the
global `+0x6d69a98`, next to the item table's), takes its 16-bit handle and appends `{handle, level,
how-learned}` to the transaction's knowledge list; the commit learns each. Items whose data has category 3
go to the same list with how-learned 0x19 (25): the value the saves record for story-given knowledge such
as Flight (see items.md and flight-gating.md).

## Giving it

`plugin give-knowledge --key K [--level L]` (Flight is 40002) makes the same call as `give-item`, with one
kind-4 entry `{key, level, 25}` instead of an item. The client's live delivery can learn knowledge too
(`LiveChannel.give_knowledge`), switched on per game build in `targets.json` (`knowledge_live`) once tested.

## Flight (2026-10-24)

On a save past the woman in white with the story's grant removed (the AP situation):

| attempt | how-learned | result |
|---|---|---|
| 1 (plugin 0.8.0) | 25 | fault inside the game (access violation; caught) |
| 2 | 19 (skill menu) | refused, 0x78c0a4e9 |
| 3 | 25 | refused, 0x78c0a4e9 |
| 4 | 25, after picking up a bug while the tool waited | **learned: the game's message, gliding works** |

Both keys are in the knowledge table (40002 → handle 4694, the skill 1005839 → 4605), so the lookup is
not what differs. The refusal code comes from the entry checker (`+0x22ce570`) and a handler
(`+0x2b4a8c3`) in the code saved so far. What changed for attempt 4 is most likely the transaction's
state: the tool used the transaction right after the player's own pickup instead of after a background
task. `give-knowledge` now records which game code used the transaction last and the transaction's
first 0x1C0 bytes, to compare a refused attempt with an accepted one.
