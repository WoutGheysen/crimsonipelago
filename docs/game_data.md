# Game data: `crimson-data`

`crimson-data` reads Crimson Desert's own data files (not saves) so we can find out how the game
grants and gates things like Flight. It is **read-only**: it never modifies the game folder.

## Where game data lives

```text
<game>/meta/0.papgt     registry: which numbered groups load, in order (first match wins)
<game>/NNNN/0.pamt      one group's index: which files it holds and where
<game>/NNNN/K.paz       the data itself (often LZ4-compressed, sometimes ChaCha20-encrypted)
```

Static game tables come in pairs inside the archives, for example
`gamedata/binary__/client/bin/skill.pabgb` (the records) and `skill.pabgh` (key → offset index).
Mods are extra groups (`0036` and up) registered at the front of `0.papgt`, which is also how a future
Crimsonipelago data override would ship.

## Commands

The game folder is found through Steam. Add `--game "D:\SteamLibrary\steamapps\common\Crimson Desert"`
if it isn't.

```powershell
crimson-data groups                                   # groups in load order, file counts
crimson-data find "*.pabgh"                           # every table index
crimson-data find "*quest*"                           # anything with quest in its path
crimson-data extract "gamedata/binary__/client/bin/*" -o gamedata --limit 3000
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
| `.pabgh` | `u16 n` + n × (`u32`/`u16`/`u8` key, `u32` offset), or `u32 n` + n × (`u32`, `u32`) |
| Record | `u32 key`, `u32 name length`, name, then table-specific fields |
