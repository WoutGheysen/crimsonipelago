# Crimsonipelago

**Crimson Desert** (PC / Steam) for [Archipelago](https://archipelago.gg), the multiworld randomizer: play Crimson
Desert in the same seed as friends playing other games. Quest steps you finish send items into their worlds, and
what they find comes back into yours, while you play.

> **Pre-alpha.** The whole story, from the prologue to the epilogue, is playable. Prologue and chapter 1 are
> confirmed in play. The later chapters come from the game's own data and are confirmed as people play them
> ([drafts](docs/research/drafts.md)); an option keeps other players safe from any check that turns out not to work.

## Playing

You need Crimson Desert on Steam, [Archipelago](https://github.com/ArchipelagoMW/Archipelago/releases) 0.6.7 or
0.6.8, and two files from the [releases](https://github.com/WoutGheysen/crimsonipelago/releases):
`crimson_desert.apworld` and `CrimsonipelagoClient.exe`.

1. Double-click `crimson_desert.apworld` to add Crimson Desert to Archipelago.
2. Make your options file from `Crimson Desert.yaml` (in the release) and give it to whoever hosts the seed.
3. Open `CrimsonipelagoClient.exe`, press **Install plugin** once, enter the server address and your name, and press
   **Connect**.
4. Start a new game and play.

The full guide is [docs/setup.md](docs/setup.md). For hosts: [docs/hosting.md](docs/hosting.md).

## What's in a seed

- **Checks:** every story objective, from the prologue to the epilogue (412 in a whole-game seed). Optionally also:
  - regional quests;
  - Abyss ruins and Abyss puzzles;
  - Standstone adventures;
  - Sealed Abyss Artifact trials and the spires.
- **Goals:** the end of any chapter, or the whole story.
- **Items:**
  - **Six abilities that gate the story:** Flight, Double Jump, Focused Force Palm, Back Hang, Aerial Maneuver and
    Call Dragon. A game-data override takes them out of the story, so they only come from Archipelago.
  - **Skill-tree abilities.**
  - **Abyss Artifacts, stat upgrades and elixirs.**
  - **Filler:** money, food, arrows and ores.
- **Delivery:**
  - Items arrive in the running game through a small plugin.
  - They're written into your save when the game is closed.
  - Loading an older save gives them again.
- **Safety:**
  - A backup is made before every save write.
  - Every received item is kept in a ledger, so nothing is lost or given twice.
  - A seed is bound to one playthrough.

## How it works

| Part | What it does |
|---|---|
| `apworld/crimson_desert` | The Archipelago world: options, items, checks, logic, generated from a data catalogue (`data/*.json`). |
| `crimsonipelago/client` | The client: Archipelago connection, a crash-safe delivery ledger, check detection from your saves, the desktop window. |
| `crimsonipelago/game_adapter` | Reads and writes Crimson Desert saves; talks to the game plugin for live delivery. |
| `plugin/` | `crimsonipelago.asi`, the game plugin: delivers items through the game's own inventory code, nothing else. |
| `crimsonipelago/game_data` | Reads the game's data tables (the story's missions and rewards) and builds the game-data overrides. |
| `docs/research` | How each part was worked out and tested in the game. |

## Developing

```bash
git clone https://github.com/WoutGheysen/crimsonipelago
cd crimsonipelago
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev,ui]"
pytest                                            # integration tests skip without an Archipelago checkout
```

The client runs from source with `python -m crimsonipelago.client.main` (the window), or with `connect`, `status`,
`logs` and the other commands (`--help`). A simulated game (`sim`) exercises the whole client without Crimson
Desert. See [docs/developer_guide.md](docs/developer_guide.md).

**CI** runs on every push:
- lint;
- the tests, against a real Archipelago server;
- Archipelago's own world tests;
- bulk seed generation, with other games in the seed;
- a Windows build of the plugin, loaded into a test host.

**Releases:** pushing a `v*` tag builds `CrimsonipelagoClient.exe` and `crimson_desert.apworld` and publishes them.

## Safety and legal

- No game files or assets are in this repository or its releases. Game-data overrides are built on your PC from your
  own game files, and Steam's "Verify integrity of game files" removes them.
- No DRM or anti-cheat is bypassed. Crimson Desert is single-player, and everything works on your own game and saves.
- Save writes happen only with the game closed and after a backup. In the running game, the plugin only adds the
  items you received, through the game's own inventory code.
- Crimson Desert is a trademark of Pearl Abyss. This is an unofficial community project.

## License

MIT, see [LICENSE](LICENSE). The release also carries the NattKh save editor's decoder (MPL-2.0, unmodified) and
Ultimate ASI Loader (MIT): see [NOTICE.md](NOTICE.md).
