# Developer guide

## Environment

```bash
git clone https://github.com/WoutGheysen/crimsonipelago
cd crimsonipelago
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev,ui]"
```

Python 3.11+ (CI uses 3.12).

### Archipelago checkout (for integration tests and seed testing)

```bash
git clone https://github.com/ArchipelagoMW/Archipelago ../Archipelago
cd ../Archipelago
git checkout 4036a354637ffa56a6892f5735bc0d03ee3e7c44     # the commit CI pins
pip install colorama "websockets==13.1" PyYAML jellyfish jinja2 schema bsdiff4 platformdirs certifi orjson typing_extensions pathspec
ln -s "$PWD/../crimsonipelago/apworld/crimson_desert" worlds/crimson_desert
# Windows (admin or developer mode): mklink /D worlds\crimson_desert ..\crimsonipelago\apworld\crimson_desert
```

The symlink means Archipelago always sees your working copy. Set `SKIP_REQUIREMENTS_UPDATE=1` so
Archipelago doesn't try to install its GUI dependencies.

### NattKh decoder (optional)

```bash
git clone --depth 1 https://github.com/NattKh/CRIMSON-DESERT-SAVE-EDITOR-AND-GAME-MODS ../nattkh
```

## Running tests

```bash
export ARCHIPELAGO_PATH=../Archipelago
export NATTKH_EDITOR_DIR=../nattkh/CrimsonSaveEditor
export QT_QPA_PLATFORM=offscreen                    # UI test without a display
pytest -rs                                          # everything
pytest tests/unit                                   # fast, no external deps
ruff check .

# APWorld tests, run by Archipelago's own runner
(cd ../Archipelago && python -m pytest worlds/crimson_desert/test)

# Bulk generation: solo, mixed-game and multi-player scenarios
crimson-seeds --archipelago ../Archipelago --seeds 25 --jobs 4
```

Tests that need Archipelago or the editor skip with a reason when the variables are unset.

| Suite | Covers |
|---|---|
| `tests/unit/test_ledger.py`, `test_delivery.py` | Ledger, state machine, crash-at-every-step recovery, replays, multiple pending batches |
| `tests/unit/test_sync.py` | Sync controller against a scripted fake server: identity checks, gaps, offline checks, goal |
| `tests/unit/test_save_container.py`, `test_parc.py` | Container round-trip, HMAC/checksum failures, schema/TOC parsing (synthetic saves) |
| `tests/unit/test_snapshot_diff.py`, `test_tools.py` | Snapshots, diffs, `crimson-inspect` and `crimson-catalogue` CLIs |
| `tests/unit/test_catalogue.py` | Catalogue validation: unique and stable IDs, rule references, capabilities |
| `tests/unit/test_editor_compat.py` | Our container code agrees with the NattKh decoder |
| `tests/unit/test_build_apworld.py` | `.apworld` packaging and manifest |
| `tests/integration/test_real_server.py` | Real `Generate.py` + `MultiServer` with Crimson Desert + ChecksFinder: checks, items both ways, reconnect, no duplicates, victory |
| `tests/integration/test_ui.py` | The PySide6 window against a real server |

## The simulated game

`SimulatedGameAdapter` implements the full adapter contract over a JSON file, including writes,
so the client can be exercised end to end before the real adapter can write.

```bash
crimsonipelago sim init
crimsonipelago sim list
crimsonipelago sim complete "Unfamiliar Lands: Search the surroundings" "In Ashes: Help Naira"
crimsonipelago sim running on        # deliveries wait, like with the real game
crimsonipelago sim goal
crimsonipelago connect --server localhost:38281 --slot Wout --adapter sim
```

## The catalogue

`apworld/crimson_desert/data/*.json` is the single source of truth for items, locations, regions,
progression rules and game-build compatibility. Rules for editing it:

- **AP IDs are forever.** Once released, an ID never changes meaning and is never reused. Removed
  IDs go in `compatibility.json` under `retired_item_ids` / `retired_location_ids`.
- Only entries with `verified: true` and an `evidence` reference count as real data. See
  [reverse_engineering.md](reverse_engineering.md).
- Items get a `capability`: `supported`, `restricted`, `detection_only` or `unsupported`. Only the
  first two are deliverable, and only deliverable items enter the randomized progression pool.
- Run `crimson-catalogue validate` after every edit. `crimson-catalogue report` shows how much is verified.

Any catalogue change alters its fingerprint. Seeds generated with the old catalogue then refuse to
sync with the new client, which is intended.

The shipped catalogue holds real game data only. Client and ledger tests use a synthetic world instead,
`tests/fixtures/placeholder_catalogue/` (fixture `catalogue`), whose items and checks exercise every client
path (unsupported items, traps, gated regions); `real_catalogue` is the shipped one.

Location ids are `10,000,000,000 × kind + the game's key` (kind 1: mission or objective), so an id says
which game record it reads. An objective's detection may name a `parent_mission_key`: the check then
also counts once that mission is complete.

## Adding a real reward procedure

`CrimsonDesertSaveAdapter.prepare()` already does the safety work: version gate, game-closed check,
backup, temp file, re-decode validation, schema check, atomic swap. To add a delivery type:

1. Prove the transformation on disposable saves (Phase 0, scope doc 3.4).
2. Implement the blob transformation for that `game_ref` kind.
3. Add the game build to `compatibility.json` with `writes_enabled: true`.
4. Mark the items `capability: supported` with evidence.
5. Add a regression test using a fixture save.

## Releases

1. Bump `version` in `pyproject.toml`, `crimsonipelago/__init__.py` and `world_version` in
   `apworld/crimson_desert/archipelago.json`.
2. Push a tag `vX.Y.Z`. The *Build release* workflow builds `CrimsonipelagoClient.exe` and
   `crimson_desert.apworld` on Windows, smoke-tests the exe and publishes a GitHub release.

Local builds:

```bash
crimson-build-apworld                                # dist/crimson_desert.apworld
pip install pyinstaller && pyinstaller packaging/crimsonipelago.spec   # dist/CrimsonipelagoClient(.exe)
```
