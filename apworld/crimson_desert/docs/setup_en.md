# Crimson Desert Setup Guide

## Requirements

- A legitimate PC copy of Crimson Desert (Steam).
- [Archipelago](https://github.com/ArchipelagoMW/Archipelago/releases) 0.6.7 or newer.
- The Crimsonipelago release: `crimson_desert.apworld` and `CrimsonipelagoClient.exe`.

## Installation

1. Double-click `crimson_desert.apworld` (or copy it to Archipelago's `custom_worlds` folder).
2. Put `CrimsonipelagoClient.exe` in a folder of its own. It keeps its database and save backups next to itself.
3. Generate a YAML from the Archipelago Launcher ("Generate Template Options") or use `examples/player.yaml`.

## Joining a multiworld

1. Start `CrimsonipelagoClient.exe`.
2. Enter the server address, port, slot name and password, then press Connect.
3. Start a **new** campaign in Crimson Desert. The client binds your save profile to the seed the first time it
   syncs and will refuse to sync a different profile to the same seed.
4. Play normally. The activity feed shows checks you send and items you receive.

## Safety

The client backs up your save before every change, refuses to write while the game is running, and refuses to
write at all on game versions that are not listed as tested. Keep your normal campaign in a different slot.
