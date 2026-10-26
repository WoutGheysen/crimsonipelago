# Setup

> Pre-alpha: the release pipeline works, but the catalogue is still a placeholder. Real Crimson
> Desert checks and rewards arrive once Phase 0 is done. Until then this guide is for testers and
> developers.

## Players

### Requirements

- A legitimate PC copy of Crimson Desert (Steam).
- [Archipelago](https://github.com/ArchipelagoMW/Archipelago/releases) 0.6.7 or newer.
- From the [Crimsonipelago releases](https://github.com/WoutGheysen/crimsonipelago/releases):
  `crimson_desert.apworld` and `CrimsonipelagoClient.exe`.

### Install

1. Double-click `crimson_desert.apworld`, or copy it into Archipelago's `custom_worlds` folder.
2. Put `CrimsonipelagoClient.exe` in a folder of its own. It keeps its ledger, settings and save
   backups in `crimsonipelago_data` next to itself.
3. Make your options file: copy `Crimson Desert.yaml` from the release (or `examples/player.yaml`),
   set your `name`, and give it to the host.

### Play

1. Start `CrimsonipelagoClient.exe`.
2. Enter the server address and port, your slot name and the room password, then press **Connect**.
3. Start a **new** campaign in a save slot you don't use for anything else. The first sync binds
   that slot to the seed.
4. Play normally. The activity feed shows checks sent, items received and deliveries.
5. Rewards are written to your save while the game is closed. Quit to desktop to receive pending
   items. A backup is made before every write.

### Optional: field-level save decoding

Check detection reads quest and knowledge state through the open-source NattKh save editor's
decoder, which Crimsonipelago does not ship. To enable it, download
[CRIMSON-DESERT-SAVE-EDITOR-AND-GAME-MODS](https://github.com/NattKh/CRIMSON-DESERT-SAVE-EDITOR-AND-GAME-MODS)
and point the client at its `CrimsonSaveEditor` folder (`--editor-dir`, or the setting in the window).

## Hosts

1. Install Archipelago 0.6.7+ and `crimson_desert.apworld` as above.
2. Put every player's YAML in Archipelago's `Players` folder and run **Generate**.
3. Host the result yourself (`MultiServer`) or upload it to archipelago.gg.

The full guide for hosts is [hosting.md](hosting.md).

Crimson Desert players need a client at least as new as the `.apworld` you generated with. A newer client keeps
working with the seed. A client that doesn't know the seed's catalogue refuses to sync and says why.

## Command line

The exe and the `crimsonipelago` command take the same arguments:

```bash
CrimsonipelagoClient.exe connect --server archipelago.gg:38281 --slot Wout
CrimsonipelagoClient.exe status
CrimsonipelagoClient.exe --help
```

See [troubleshooting.md](troubleshooting.md) when something doesn't work.
