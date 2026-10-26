# Setup

> Pre-alpha. Prologue and chapter 1 are confirmed in play; the rest of the story is playable as drafts that are
> being confirmed (`docs/research/drafts.md`).

## Players

### What you need

- A PC copy of Crimson Desert (Steam).
- [Archipelago](https://github.com/ArchipelagoMW/Archipelago/releases) 0.6.7 or 0.6.8.
- From the [Crimsonipelago releases](https://github.com/WoutGheysen/crimsonipelago/releases):
  `crimson_desert.apworld` and `CrimsonipelagoClient.exe`.

Nothing else: the client carries the save editor's decoder, the game plugin and the ASI loader (NOTICE.md).

### Install

1. Double-click `crimson_desert.apworld`, or copy it into Archipelago's `custom_worlds` folder.
2. Put `CrimsonipelagoClient.exe` in a folder of its own. It keeps its settings, logs and save backups in
   `crimsonipelago_data` next to itself.
3. Make your options file from `Crimson Desert.yaml` in the release (or `examples/player.yaml`): set your `name` and
   your `goal`, and give it to the host.

### Play

1. **Start `CrimsonipelagoClient.exe`.** It finds the game and your saves by itself. If it can't, choose the game
   folder under *Crimson Desert*.
2. **Game plugin:** press **Install plugin** once, with the game closed. With it, items arrive while you play.
3. **Connect:** enter the address and port the host gave you, your player name and the room password, then press
   **Connect**.
4. **Game-data override:** if a banner says the seed needs one, close the game and press its button. The override
   takes the abilities your seed hands out out of the story.
5. **Start a new game** and play. Checks go out each time the game saves. The *Activity* tab shows what's sent and
   received, and the *Checks* tab shows every check of your seed, chapter by chapter.

### Buttons for when something's off

- **Move seed to my newest save…:** you saved over the playthrough the seed was on, or started over. The seed
  moves to your newest save's playthrough. Checks already sent stay sent, and you choose whether items already
  received are given again.
- **Send logs…:** makes a zip of the play logs for a bug report. It never includes your password or your saves.
- **It's in my inventory / It's missing: deliver again:** only after an interrupted delivery.

### Commands

Everything the window does also works from a terminal:

```powershell
CrimsonipelagoClient.exe connect --server archipelago.gg:38281 --slot Wout
CrimsonipelagoClient.exe status
CrimsonipelagoClient.exe logs
CrimsonipelagoClient.exe bundled
CrimsonipelagoClient.exe --help
```

From source, use `py -m crimsonipelago.client.main` in place of `CrimsonipelagoClient.exe`.

## Hosts

1. Install Archipelago 0.6.7+ and `crimson_desert.apworld` as above.
2. Put every player's YAML in Archipelago's `Players` folder and run **Generate**.
3. Host the result yourself (`MultiServer`) or upload it to archipelago.gg.

The full guide for hosts is [hosting.md](hosting.md).

Crimson Desert players need a client at least as new as the `.apworld` you generated with. A newer client keeps
working with the seed. A client that doesn't know the seed's catalogue refuses to sync and says why.

See [troubleshooting.md](troubleshooting.md) when something doesn't work.
