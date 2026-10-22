# Troubleshooting

Messages appear in the client's activity feed (or the console with `crimsonipelago connect`).
`crimsonipelago status` shows the ledger for the most recent session at any time.

## Connection

**Cannot connect / keeps reconnecting.**
Check the address and port shown on the room page. A bare `host:port` is tried as `wss://` first,
then `ws://`. For a local server use `localhost:38281`. The client keeps retrying on its own, and
checks you complete meanwhile are stored locally and sent once it reconnects.

**"Slot 'X' is a <game> slot, not Crimson Desert."**
The slot name you typed belongs to another player's game. Use the name from your own YAML.

**"This seed was generated with a different Crimson Desert APWorld than this client was built with."**
The `.apworld` used to generate the room and your client come from different releases. Install the
client release that matches the APWorld version used for generation (the host can see it in the
generation log).

**"The server did not report a seed name; refusing to sync without a session identity."**
The server is too old or not a real Archipelago server. Use Archipelago 0.6.7 or newer.

## Save and campaign

**"No Crimson Desert save was found. Start or load the campaign for this seed."**
Start the campaign and save once. If your saves are somewhere unusual, pass `--save <path to save.save>`.
`crimson-inspect locate` lists every save it can find.

**"Waiting for the campaign: this save does not have enough history yet …"**
A brand-new game looks the same in every playthrough for its first minutes. Keep playing; the client
recognises the playthrough from the first save about 15 minutes in. Items received meanwhile wait.

**"This playthrough (…) already belongs to seed …"**
Each seed is bound to one playthrough the first time it syncs, so progression can never leak between
seeds. Start a new game for the new seed.

**"This seed belongs to playthrough …, but the newest save is from …"**
Your most recent save is from another playthrough. Load a save of this seed's playthrough and let the
game save once (or make a manual save).

**"The game-data override 'flight-grant-removed' is not installed …"**
Without it the story gives you Flight at the woman in white, so the Flight item from the multiworld
changes nothing. With the game closed: `crimson-mod install flight-grant-removed`.

**"The loaded save is missing Flight; it will be given again"**
You loaded a save from before Flight was delivered. The client adds it again the next time the game is
closed. Nothing is lost or doubled.

## Rewards

**"… kept for later, cannot be delivered yet"**
That item has no verified delivery procedure in this release. It stays in the ledger as
`UNSUPPORTED` and is delivered automatically by a later release that supports it. So far only Flight
is delivered; the rest of the catalogue is still placeholder.

**"Crimson Desert is running; rewards are applied once you quit to desktop"**
Save writes only happen while the game is closed. Pending rewards are applied when you quit.

**"cannot tell whether Crimson Desert is running; refusing to write"**
The client could not list processes, so it treats the situation as unsafe. Close the game and make
sure the client is not sandboxed from seeing other processes.

**"save writes are disabled for game version …"**
Your game build is not listed as tested in `compatibility.json`. This is deliberate after a game
update: the save format may have changed. Wait for a client release that lists your build.

**A delivery is in `RECOVERY_REQUIRED`.**
The client was interrupted mid-write and found the save changed by something else afterwards, so it
cannot tell whether the reward landed. Check in-game, then settle it:

```bash
crimsonipelago status                         # shows the delivery id
crimsonipelago resolve <id> delivered         # the reward is in your save
crimsonipelago resolve <id> retry             # it is not; try again
```

Every write is preceded by a backup in `<data dir>/backups/`.

## Where things are

| What | Where |
|---|---|
| Ledger, settings, backups | Next to `CrimsonipelagoClient.exe` (`crimsonipelago_data/`), or `--data-dir` |
| Game saves | `%LOCALAPPDATA%\Pearl Abyss\CD\save\<account>\slot<N>\save.save` |
| Game version | Steam's `steamapps\appmanifest_3321460.acf` (`--steam-manifest` to override) |

When reporting a bug, include the output of `crimsonipelago status`, your game build id and
the client version (`--version`). Don't attach your save publicly unless you are fine sharing it.
