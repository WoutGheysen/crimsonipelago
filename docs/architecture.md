# Architecture

```text
 Archipelago server ──WebSocket──► APConnection ──packets──► SyncController ◄──► Ledger (SQLite)
                                                                  │
                                         CheckTracker ◄───────────┤ (poll)
                                         DeliveryEngine ◄─────────┘
                                                │
                                          GameAdapter (interface)
                                 ┌──────────────┴───────────────┐
                    CrimsonDesertSaveAdapter            SimulatedGameAdapter
                    (real save.save files)               (JSON fake, for tests)
                                 │
              save_container ─ parc ─ [optional] nattkh_backend
```

The APWorld (`apworld/crimson_desert`) is separate. It runs inside Archipelago at generation time
and never touches the game. The client and the APWorld share one piece of code, `catalogue.py`,
which loads and validates `data/*.json` with no Archipelago imports. Item and location IDs,
names and capabilities therefore can never disagree between generator and client.

## Components

### APWorld: `apworld/crimson_desert/`

| Module | Role |
|---|---|
| `catalogue.py` | Loads and validates the JSON catalogue, plus the JSON rule language (`item`, `event`, `location`, `all`, `any`). |
| `world.py` | `CrimsonDesertWorld`: item pool, regions, rules, victory event, slot data. |
| `options.py` | YAML options. Only options backed by working functionality are exposed. |
| `items.py`, `locations.py`, `regions.py`, `rules.py` | Thin translation from catalogue entries to Archipelago objects. |
| `presets.py` | Casual and Standard presets. Chaos waits until Standard is stable. |
| `data/*.json` | The catalogue: items, locations, regions, progression, compatibility. |

Items whose capability is not `supported`/`restricted` are never placed in the randomized
progression pool (scope doc 6.5). Generation logs a loud warning while the catalogue is the
placeholder.

### Client: `crimsonipelago/client/`

| Module | Role |
|---|---|
| `ap_connection.py` | WebSocket transport with automatic reconnect and `ws://`/`wss://` fallback. |
| `protocol.py`, `datapackage.py` | Packet builders and parsing, data package cache. |
| `synchronization.py` | `SyncController`: handshake, session validation, received items by index, sending checks and the goal, driving delivery, pausing on anything unsafe. |
| `database.py` | `Ledger`: SQLite. Sessions, received items and their delivery state, in-flight write batches, detected checks, an event log. |
| `check_tracker.py` | Records each check in the ledger the moment the adapter reports it, online or not. |
| `item_delivery.py` | `DeliveryEngine`: the exactly-once state machine. |
| `events.py` | Event bus used by the CLI log and the UI activity feed. |
| `main.py` | CLI entry point (`ui`, `connect`, `status`, `resolve`, `sim`). |
| `ui/main_window.py` | PySide6 desktop window. |

### Game adapter: `crimsonipelago/game_adapter/`

| Module | Role |
|---|---|
| `interface.py` | The `GameAdapter` contract and the two-phase write protocol. |
| `crimson_adapter.py` | Real save-file adapter. Reads the newest save; delivers the item kinds verified in-game (so far: knowledge, e.g. Flight); everything else is `UNSUPPORTED`. |
| `save_edit.py` | Structural save edits: appends list elements (e.g. learns a knowledge entry) and keeps pointers, sizes and the table of contents consistent; the result is decoded again and compared. |
| `campaign.py` | Tells which playthrough a save belongs to from its history, so every slot of one playthrough maps to one campaign. |
| `sim_adapter.py` | Simulated game that supports everything, used by tests and `crimsonipelago sim`. |
| `save_container.py` | `save.save` header, ChaCha20 decrypt/encrypt, HMAC check, LZ4. |
| `parc.py` | Reads the serializer's self-describing schema and table of contents, and fingerprints each top-level save object. |
| `nattkh_backend.py` | Optional field-level decoding through the user's own NattKh editor checkout. |
| `snapshot.py`, `save_diff.py` | Normalized JSON snapshots and human-readable diffs. |
| `save_locator.py` | Finds `…/Pearl Abyss/CD/save/<account>/slot<N>/save.save` and makes read-only backups. |
| `version_detection.py` | Reads the Steam build id from `appmanifest_3321460.acf`, classifies it against `compatibility.json`. |
| `process_check.py` | Is the game running? Unknown is treated as unsafe. |

## Delivery: exactly-once effective

```text
RECEIVED → VALIDATED → PENDING → APPLYING → VERIFIED → COMPLETE
                                    └──────► RECOVERY_REQUIRED
RECEIVED → UNSUPPORTED   (parked, re-validated when the catalogue or adapter changes)
```

Writes are two-phase:

1. `adapter.prepare(batch)` builds the modified save in a temp file and returns the save's
   fingerprint before (`pre`) and after (`post`) the change.
2. The ledger durably records `pre` and `post` and moves the items to `APPLYING`, in one transaction.
3. `adapter.commit()` atomically swaps the temp file in.
4. The adapter verifies the reward, and the ledger moves the items to `COMPLETE`.

After a crash, the current fingerprint answers the question unambiguously. Equal to `pre`: the
write never happened, so it is safe to retry. Equal to `post`: it happened, so mark it complete.
Anything else: the game or the user changed the save in between, so the client stops and asks
(`crimsonipelago resolve <id> delivered|retry`). A boolean "received" flag can't tell those cases apart.

## Session isolation

The first successful sync binds a seed to one **campaign** (playthrough). Each connect validates the
seed, slot, game name, the catalogue fingerprint the seed was generated with (sent in slot data) and
the bound campaign. A mismatch pauses sync with a message saying what is wrong and how to fix it.
Progression never moves between seeds.

A save slot is not a campaign: the game rotates autosaves over `slot0`–`slot2` and keeps manual saves in
`slot100`+. The save has no campaign id either, so `campaign.py` recognises a playthrough by its
history: the game-time moments quests were completed and knowledge was learned (batches with one shared
time are scripted and ignored). Two playthroughs share none of these; later saves of one playthrough
keep them. A campaign is registered once a save has at least 5 of these anchors (about 15 minutes into a
new game) and is stored in `campaigns.json` in the client's data folder.

## Unlocks lost to an older save

Abilities such as Flight are delivered by adding them to the save. If the player later loads a save from
before that delivery, the ability is gone while the ledger says delivered. For item kinds whose
delivery is idempotent (learning knowledge twice is a no-op), the engine checks the loaded save every
tick and re-queues what is missing (`DeliveryEngine.reconcile`). Consumables are never re-queued.

## Received items

Items are stored by their server index. Replays (an index we already have, with the same item)
are ignored, so a reconnect never re-delivers anything. A gap (an index beyond the next expected
one) is never stored as a hole: the client sends `Sync` and re-sends its unconfirmed checks, and
the server replays the full list. If the server reports a *different* item at an index the ledger
already holds, the client pauses with an identity error. That means a different seed or a reset
room, and guessing would risk giving the wrong rewards.

## Offline play

Checks detected while disconnected sit in the ledger as `detected` and are sent on reconnect.
The server's `RoomUpdate`/`Connected` checked-locations list confirms them, including checks
completed by `!collect`/`!release`.
