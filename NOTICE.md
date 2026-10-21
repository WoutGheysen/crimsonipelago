# Third-party notices

## CRIMSON-DESERT-SAVE-EDITOR-AND-GAME-MODS (NattKh / RicePaddySoftware)

- Source: https://github.com/NattKh/CRIMSON-DESERT-SAVE-EDITOR-AND-GAME-MODS
- License: Mozilla Public License 2.0
- Reviewed at commit `96e7f78fcb00d6cb5615171a7f359f0c2de5b114`

**No code from this project is copied into Crimsonipelago.**

- `crimsonipelago/game_adapter/save_container.py` and `parc.py` are independent implementations of
  the save container and serializer layout. The format facts they rely on (header offsets, the
  ChaCha20 / HMAC-SHA256 / LZ4 scheme and its key-derivation constants, the schema and TOC layout)
  were learned from that project's source and documentation, and are credited to it here.
- `crimsonipelago/game_adapter/nattkh_backend.py` optionally imports the editor's own decoder
  (`save_parser`, `save_crypto`, `models`) from a checkout **the user provides** (`--editor-dir`).
  That code is used unmodified and stays under the MPL-2.0. It is not redistributed in this
  repository or in Crimsonipelago releases.

- `crimsonipelago/tools/candidates.py` (`crimson-candidates`) reads the editor's game-data tables
  from the user's checkout and writes candidate lists to a local, git-ignored folder. Those lists are
  not committed or redistributed. `docs/research/` contains our own analysis, citing individual keys.

If MPL-2.0 files are ever vendored into this repository, they must keep their license headers and
remain under the MPL-2.0 (file-level copyleft). The rest of the project can stay MIT.

## Archipelago

- Source: https://github.com/ArchipelagoMW/Archipelago
- License: MIT

The APWorld is written against Archipelago's World API and loaded by Archipelago at runtime.
No Archipelago code is copied. The test suite and CI use an unmodified, pinned Archipelago
checkout to generate seeds and run a real `MultiServer`.

## Runtime dependencies

| Package | License |
|---|---|
| websockets | BSD-3-Clause |
| lz4 (python-lz4) | BSD-3-Clause |
| cryptography | Apache-2.0 OR BSD-3-Clause |
| PySide6 (optional UI) | LGPL-3.0 |

The Windows executable bundles PySide6 / Qt, which are licensed under the LGPL-3.0. To use a
modified or different Qt / PySide6, run Crimsonipelago from source (`pip install -e ".[ui]"`)
against your own PySide6 installation. The complete source of this project is public.
