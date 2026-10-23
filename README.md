# Crimsonipelago

[Archipelago](https://archipelago.gg) multiworld integration for **Crimson Desert** (PC / Steam):
an APWorld for seed generation, a client that talks to Archipelago servers, and a save-file game adapter.

> **Status: pre-alpha, first playable slice.**
> The APWorld covers the prologue and chapter 1 (42 real checks, read from your save) with
> **Flight** as the one real unlock: a game-data override stops the story from handing it out and the
> client adds it to your save when it arrives. Abyss Artifacts and Copper are added to your bag the same way.
> See [Roadmap](#roadmap).

## What works today

| Area | State |
|---|---|
| APWorld (`crimson_desert.apworld`) | Loads in Archipelago 0.6.7+ (CI-tested on 0.6.7, 0.6.8 and current main), generates solo and mixed-game seeds (bulk-tested: 80/80 across 8 scenarios). Real data for the first slice: 42 checks, Flight, filler. |
| Network client | Full Archipelago protocol, reconnect, offline check queue, replay/gap handling, victory reporting. Tested end to end against a real `MultiServer` with a second game. |
| Delivery ledger | SQLite ledger and an idempotent state machine (`RECEIVED → … → COMPLETE / RECOVERY_REQUIRED`) with two-phase, crash-safe writes. Fault-injection tests. |
| Session isolation | Seed, slot, game, catalogue fingerprint and **playthrough** are bound together; the playthrough is recognised from save history, whichever slot the game saved to. Mismatches pause sync with an actionable message. |
| Save reading | Decrypts and validates the `save.save` container (ChaCha20, HMAC, LZ4) and fingerprints every top-level save object. Optional field-level decoding through your own checkout of the NattKh save editor. |
| `crimson-inspect` | Locate, back up, snapshot and diff saves; `watch` records a snapshot every time the game saves. |
| Save writing | **Flight is delivered** (verified in-game on build 25477059): added to the save like the story does, after a backup, with the game closed, decoded again before the swap. A save loaded from before the delivery gets it again. Other items stay parked until verified. Needs the `flight-grant-removed` game-data override (`crimson-mod`), so the story doesn't hand it out first. |
| Desktop UI | PySide6 window: connection, game status, activity feed, pending deliveries. |
| Simulated game | A fake save used to exercise the whole client without the game. |

## Quick start (developers)

```bash
git clone https://github.com/WoutGheysen/crimsonipelago
cd crimsonipelago
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev,ui]"
pytest                                            # unit tests; integration tests skip without Archipelago
```

Full test run, including the real-server tests, needs an Archipelago source checkout. See
[docs/developer_guide.md](docs/developer_guide.md).

Try the client against the simulated game:

```bash
crimsonipelago sim init
crimsonipelago sim complete "Unfamiliar Lands: Search the surroundings"
crimsonipelago connect --server localhost:38281 --slot Wout --adapter sim
crimsonipelago status
```

## Tools

| Command | Purpose |
|---|---|
| `crimsonipelago` | The client: `ui` (default), `connect`, `status`, `resolve`, `sim` |
| `crimson-inspect` | Read-only save inspector: `locate`, `info`, `backup`, `snapshot`, `diff`, `watch` |
| `crimson-catalogue` | Validate the data catalogue and report how much of it is verified |
| `crimson-seeds` | Bulk seed-generation tester (solo, mixed-game and multi-player scenarios) |
| `crimson-build-apworld` | Package `apworld/crimson_desert` into `dist/crimson_desert.apworld` |
| `crimson-data` | Read the game's own data archives: list, extract, and search static tables ([docs/game_data.md](docs/game_data.md)) |
| `crimson-testsave` | Make verified, edited copies of a save for in-game write experiments |
| `crimson-candidates` | Draft unverified catalogue candidates (quests, missions, abilities, items) from the NattKh editor's data tables |

## Repository layout

```text
apworld/crimson_desert/   APWorld: world, options, items, locations, regions, rules, data/*.json catalogue
crimsonipelago/client/    Archipelago connection, protocol, ledger, sync controller, delivery engine, UI
crimsonipelago/game_adapter/  adapter interface, real save adapter, simulated adapter, save container/parser, diff
crimsonipelago/tools/     crimson-inspect, crimson-catalogue, crimson-seeds, crimson-build-apworld
tests/                    unit, integration (real Archipelago server, UI) and fixtures
packaging/                PyInstaller spec for CrimsonipelagoClient.exe
docs/                     architecture, setup, developer guide, reverse engineering, troubleshooting
```

## Roadmap

Milestones follow the project scope document.

| | Milestone | State |
|---|---|---|
| M0 | Feasibility report | **Passed.** Detection from saves, delivery into saves (tests A and D), and withholding a story reward with a game-data override (E2): see `docs/research/`. |
| M1 | Save inspector | Built and used on the real save series. |
| M2 | Minimal APWorld | Built; ships the first slice. |
| M3 | Network client | Built and tested against a real server. |
| M4 | Game bridge | Mission detection verified; playthrough identity from save history. |
| M5 | Reward delivery | Flight (knowledge) verified in-game; inventory items next. |
| M6 | Progression randomizer | Flight withheld by `crimson-mod`; more abilities to follow the same way. |
| M7 | Logic catalogue | First slice (prologue and chapter 1); the rest of the story next. |
| M8 | Playable alpha | Not started. |
| M9 | Public beta | Installer pieces built: Windows exe and `.apworld` via GitHub Actions. |

If Phase 0 shows original rewards cannot be withheld and restored, the project is scoped down to
a clearly labelled Archipelago-compatible check tracker instead of a full randomizer.

## Builds

- **CI** (`.github/workflows/ci.yml`) runs lint, all tests against a pinned Archipelago, Archipelago's
  own world tests, bulk seed generation and a player-style `.apworld` install on every push.
- **Release** (`.github/workflows/release.yml`) builds `CrimsonipelagoClient.exe` and
  `crimson_desert.apworld` on Windows. Run it from the Actions tab for a test build, or push a
  `v*` tag to publish a GitHub release.

## Safety and legal

- No game files, assets or binaries are in this repository or its releases.
- No DRM or anti-cheat is bypassed. The project works on save files you own.
- The client never writes to a save without a backup and refuses to write while the game runs or
  on game builds not listed as tested.
- Crimson Desert is a trademark of Pearl Abyss. This is an unofficial community project.

## License

MIT, see [LICENSE](LICENSE). Third-party attribution is in [NOTICE.md](NOTICE.md).
