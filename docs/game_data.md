# Game data: `crimson-data`

`crimson-data` reads Crimson Desert's own data files (not saves) so we can find out how the game
grants and gates things like Flight. It is **read-only**: it never modifies the game folder.

## Where game data lives

```text
<game>/meta/0.papgt     registry: which numbered groups load, in order (first match wins)
<game>/NNNN/0.pamt      one group's index: which files it holds and where
<game>/NNNN/K.paz       the data itself (often LZ4-compressed, sometimes ChaCha20-encrypted)
```

Static game tables come in pairs inside the archives: a body with the records and an index
(key → offset). The current Steam build (25477059) names them `skill.staticinfobody` +
`skill.staticinfoheader`; older builds and community mods use `skill.pabgb` + `skill.pabgh`.
The table commands accept both.
Mods are extra groups (`0036` and up) registered at the front of `0.papgt`, which is also how a future
Crimsonipelago data override would ship.

## Commands

The game folder is found through Steam. Add `--game "D:\SteamLibrary\steamapps\common\Crimson Desert"`
if it isn't.

```powershell
crimson-data groups                                   # groups in load order, file counts
crimson-data types                                    # file count per extension (short)
crimson-data find "*.staticinfoheader"                # every table index (current build)
crimson-data find "*quest*.staticinfo*"               # quest tables
crimson-data extract "*.staticinfo*" -o gamedata --limit 5000
crimson-data tables gamedata                          # tables found, with record counts
crimson-data records gamedata skill --name CrowWing   # record keys and names
crimson-data search gamedata 40002                    # every record that contains the value 40002
crimson-data dump gamedata knowledgeinfo 40002 --hex  # one record: strings, references, hex
```

`search` and `dump` report 32-bit values that equal the key of a known record. Large keys (skills,
knowledge, quests) are meaningful; small numbers often match by coincidence, so treat those as hints.

## Format notes

Implemented independently from the format description in
[crimson-rs](https://github.com/potter420/crimson-rs) (MIT) and the NattKh modding notes (MPL-2.0).
Every checksum (registry, group index, directory names, chunks) is verified on read; the test suite
checks the reader against a real mod pack built by the community tools.

| Piece | Layout |
|---|---|
| Checksum | Jenkins lookup3 `hashlittle`, initial value `len + 0xDEBA1DCD` |
| PAPGT | header (unknown, checksum, count); entries (optional, language mask, name offset, PAMT checksum); name buffer |
| PAMT | header (checksum, chunk count, 0, unknown, 3-byte encrypt info); chunks; directory and file name tries; directory table; file table |
| File flags | low nibble compression (0 none, 1 partial, 2 LZ4, 3 zlib, 4 QuickLZ); high nibble crypto (0 none, 3 ChaCha20) |
| ChaCha20 | nonce = checksum(file name) ×4; key = fixed base key XOR nonce XOR (xor of the 3 encrypt-info bytes) |
| Index (`.staticinfoheader` / `.pabgh`) | `u16 n` + n × (`u32`/`u16`/`u8` key, `u32` offset), or `u32 n` + n × (`u32`, `u32`) |
| Record | `u32 key`, `u32 name length`, name, then table-specific fields |

## Overrides: `crimson-mod`

`crimson-mod` installs a small game-data override: a few patched tables in an extra archive group
(`0090` and up), registered first in `meta/0.papgt` so the game prefers it. The game's own groups are
never modified.

```powershell
crimson-mod list                              # bundled patches
crimson-mod check flight-grant-removed        # dry run against your game: shows exactly what changes
crimson-mod install flight-grant-removed      # game must be closed
crimson-mod status
crimson-mod uninstall                         # removes it; 0.papgt is restored byte for byte
```

Safety rules built in:

- **Same-size patches only:** whole 32-bit values, and text of equal length, inside one record. Offsets
  never move, and the table index is shipped unchanged next to the body.
- **Each patch names its target:** the record it expects and how many replacements it must make, so it
  stops instead of guessing after a game update.
- **Install verifies itself:** the game data is re-read and the patched files must resolve to the new
  group. A failed install undoes itself.
- **Reversible:** a timestamped copy of `0.papgt` is kept before every change, and only folders carrying
  Crimsonipelago's marker file are ever deleted.
- **Game must be closed:** install and uninstall refuse while Crimson Desert is running.

Steam's "Verify integrity of game files" also returns the game to its original state.
