# The game plugin

`crimsonipelago.asi` runs inside Crimson Desert and lets the client talk to the running game.

- **R0** (passed 2026-10-23): loads, logs the game build, talks to the client (0.04 ms round trip).
- **R1** (passed 2026-10-23): memory search for research; a live Copper change that survives a save.
  The plugin never changes anything by itself: a write happens only when
  the client asks, and only if the value there is exactly the one the client expects.
- **R2** (done 2026-10-24): write watches: which game code changes your money, and what called it; execute
  watches: what the game's inventory functions are called with. Notes: [research/r2-money.md](research/r2-money.md).
- **R3** (passed 2026-10-24): 100 Copper while you play, through the game's own inventory transaction: bag,
  total and the game's own message updated at once; still there after a save and reload.
- **R4** (passed 2026-10-24): any stackable item by its key (an ore the playthrough never had included);
  gear is refused by the game's checks for now (it needs the game's own item creation).
- **Live delivery** in the client (works, 2026-10-24): `crimsonipelago connect` gives inventory items while
  you play.

## Install (Windows)

Game folder: usually `C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert\bin64`, the folder with
`CrimsonDesert.exe`.

1. **ASI loader** (skip it if a mod manager already installed one): download `dinput8-x64.zip` from the
   [Ultimate ASI Loader releases](https://github.com/ThirteenAG/Ultimate-ASI-Loader/releases), put its
   `dinput8.dll` in `bin64` and **rename it to `version.dll`**: the game doesn't load `dinput8.dll`
   (tested 2026-10-23, game 2.03.02).
2. Put `crimsonipelago.asi` in `bin64`.
3. Start the game. The plugin writes `crimsonipelago_plugin.log` next to itself.
4. With the game running, in your `crimsonipelago` folder:
   ```powershell
   py -m crimsonipelago.client.main plugin
   ```
   It prints the game version and the round-trip time. `connect` also reports
   `Game plugin connected` while it runs.

## R1: change your Copper live

With the game running and a save loaded:

```powershell
py -m crimsonipelago.client.main plugin find-copper
```

1. It asks how much money you have.
2. It looks near the item numbers the client gave to stacks it added to your save (900,000,001 and up),
   and searches the whole memory for the amount.
3. It asks you to change your money in the game (buy or sell something). Press Enter instead when nothing
   changed: that drops places that change by themselves. It needs two changes, or one when the amount sits
   next to one of our item numbers.
4. It tries the candidates one by one: adds 100 copper, checks whether the other places follow (the game
   copies the real amount into the others), asks whether the bag shows it, and puts the old amount back
   when it doesn't.

### Result: all at once (2026-10-23), R1 passed

5.00 → 3.68 left 8 places; 6 still held the amount when tried (both item records next to 900000002 and
4 others). Writing 4.68 into all 6 at once:

| | live | after save and reload |
|---|---|---|
| coin stack in the bag | 4.68 | 4.68 |
| money at the top right | unchanged | 4.68 |

So a live change to an existing stack works and is saved. The money total at the top right is worked out
separately (on load, and by the game's own code when money changes), so changing the numbers directly
skips part of the game's bookkeeping. R2 looks for that code: the function the game runs when you receive
something, which updates everything at once and also covers items you don't have a stack of yet.

### Third attempt (2026-10-23)

13 places held one of our item numbers; 2 of them had the money 16 bytes after it, in identical records
(item number, two 4-byte fields, then the amount as 8 bytes): `900000002, 0x7bc, …, 700`. After 8.00 → 7.00,
9 places followed: those 2 and 7 others. Writing 800 into the second record changed the coin stack in the
bag, but not the money at the top right, and the save written afterwards still had 7.00. So the game keeps
the stack, the money total and what it saves apart. The tool can now change all of them at once (`a`), and
asks about the top right and the coin stack separately.

### Second attempt (2026-10-23)

Starting from 8.00, 2,806 places held 800; after 7.00 only 7 were left, and the same 7 followed 6.00, 5.98
and 3.98: the game keeps the amount in several places. The tool now stops once the same places follow
every change (up to 16) and uses the copies to tell which one is the source.

### First attempt (2026-10-23, plugin 0.2.0-r1)

The first amount was typed as `8` and read as 8 copper instead of 8.00 silver, so the search started from the
wrong value; the 3 places left after 5.70 were coincidences (one had moved on by the time it was tried, so
the write was refused and nothing changed). Amounts are now always read as silver, as the bag shows them,
and the client says back how much copper that is.

## R2: what changes your money

With the game running and a save loaded (plugin 0.4.0 or newer):

```powershell
py -m crimsonipelago.client.main plugin watch-money
```

1. It finds your money the same way as `find-copper` (one money change is enough when it sits next to one
   of our item numbers).
2. It watches up to 4 of those places. Nothing in the game changes: the processor reports every write to
   them, and the plugin notes which instruction wrote, the call chain above it, and return addresses on
   the stack.
3. You buy something, sell something and pick up coins; after each, you type what you did and press Enter.
4. It prints which game functions wrote and what called them, and saves everything to
   `crimsonipelago_data\research\r2-money-….json`.

The watches use the processor's debug registers, like a debugger's data breakpoints. If Denuvo minds,
the game may close: that loses nothing (no write happens), but tell me. The watches are removed when the
command ends or the client disconnects.

First results: [research/r2-money.md](research/r2-money.md).

Then, with the same report:

```powershell
py -m crimsonipelago.client.main plugin dump-code
```

saves the game's code for each function on the recorded paths (`r2-code-….json`, plugin 0.5.0 or newer),
to work out what they expect. It stays on your PC and in our conversation, never in the repository.

### R2c: what the game's inventory functions are called with

```powershell
py -m crimsonipelago.client.main plugin watch-calls
```

(plugin 0.6.0 or newer) watches calls of the inventory transaction functions found so far
(`crimsonipelago/game_adapter/runtime/targets.json`, per game build) and records their arguments and the
memory behind them while you loot, buy, sell and finish things, each labelled. The report,
`r2-calls-….json`, holds bits of your game's memory: send it to me, it doesn't go in the repository.

What comes next: the common caller of those writes is the game's code for "money changed"; above it,
the code for "received an item" (which also refreshes the total at the top right). R3 calls that code.

## R3: Copper while you play

```powershell
py -m crimsonipelago.client.main plugin give-copper
```

(plugin 0.8.0 or newer, on a playthrough that already has a Copper stack from an Archipelago delivery)

1. You make a manual save, so you can go back to it.
2. It waits for the game to use its own inventory transaction (it does every few seconds), which tells it
   which transaction and which game thread.
3. It finds your Copper stack and copies it as the item to give, with 100 coins.
4. It asks the game to "add, check and commit" one change entry with that item, the way picking something
   up does: the plugin makes the call on the game-logic thread, the next time that thread starts one of its
   own tasks (an execute watch on the task runner), and nowhere else.
5. It reports whether the game accepted it, and asks what you see: the bag, the total at the top right,
   a message.

The plugin only calls functions inside the game's executable, and only writes into memory of its own (plus
one byte of the transaction, set the way the shop sets it). If the game's code faults during the call, the
plugin catches it and tells you not to save.

## R4: any item

```powershell
py -m crimsonipelago.client.main plugin item-table
py -m crimsonipelago.client.main plugin give-item --key 75001 --count 1
```

(plugin 0.8.0 or newer)

- `item-table` reads the game's own item list: for every item key (the numbers in the save, e.g. 1 for
  Copper, 75001 for the Abyss Artifact), the 16-bit handle the game uses in memory. It checks its reading
  against the items already loaded and says whether it's confirmed.
- `py -m crimsonipelago.client.main items iron ore` finds item keys by name, from the item list in your
  CrimsonSaveEditor folder (`--category Weapon` narrows it down).
- `give-item` works like `give-copper` for any item: it looks up the handle, copies a record of the same
  item if one of the client's deliveries left one in memory (otherwise the Copper record), sets the handle
  and the count, and asks the game to add it. Finding the records takes about a minute (one search of the
  game's memory).

## R5: abilities (knowledge)

```powershell
py -m crimsonipelago.client.main plugin give-knowledge --key 40002
```

learns a knowledge entry (Flight is 40002) the way the skill menu does: a change entry of kind 4 on the
game's transaction. Notes: [research/r5-knowledge.md](research/r5-knowledge.md).

## Live delivery in the client

With the plugin installed and the game running, `crimsonipelago connect` connects to it by itself
(`Game plugin connected (…); items arrive while you play`) and gives every inventory item in the seed (Abyss
Artifacts, Copper) the moment it arrives, the R3/R4 way, with a built-in stackable item record (no memory
search). The ledger keeps such items as *in the game* until the game writes a save after them; if the game
closes first, they go back to the queue and are added to the save instead.

- Flight (knowledge) is learned live as well (on for game build 1790086912 in `targets.json`). The game
  sometimes refuses it (r5-knowledge.md); the client tries three times on later ticks, then leaves it for
  the save.
- An item the game refuses live (gear, for now) waits for the save as well.
- While the client runs, the plugin is busy with it: the `plugin …` research commands can't connect at the
  same time (the plugin serves one client).
- `--no-live` turns it off.

Tested 2026-10-24 with the first seed and the server's `/send`: Abyss Artifact and Copper arrived in the
game within a few seconds (while the game is in the foreground: it pauses in the background); after
quitting to desktop without saving, the client gave the Copper again through the save.

## Remove

Delete `crimsonipelago.asi` (and the loader DLL if nothing else uses it) from `bin64`. Steam's "Verify
integrity of game files" doesn't remove extra files, but the game never needs them.

## Build

```bash
cmake -S plugin -B build/plugin -A x64       # Visual Studio 2022
cmake --build build/plugin --config Release   # build/plugin/Release/crimsonipelago.asi
```

CI builds it on Windows, loads it into a stand-in `CrimsonDesert.exe`, and checks the client can talk to it;
the `.asi` is attached to every CI run as the `crimsonipelago-plugin` artifact.

## Protocol

Newline-delimited JSON on `127.0.0.1:38301` (localhost only). On connect the plugin sends `hello` with the
game facts; the client sends `{"cmd":"ping","id":N}` or `{"cmd":"info"}`, and for research `scan`,
`rescan`, `read` and `write` (4- or 8-byte values, compare-and-set), and `watch`, `watch_apply`,
`unwatch` and `hits` for write watches. See `plugin/src/main.cpp`.
