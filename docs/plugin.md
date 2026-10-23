# The game plugin

`crimsonipelago.asi` runs inside Crimson Desert and lets the client talk to the running game.

- **R0** (passed 2026-10-23): loads, logs the game build, talks to the client (0.04 ms round trip).
- **R1**: memory search for research. The plugin never changes anything by itself: a write happens only when
  the client asks, and only if the value there is exactly the one the client expects.

## Install (Windows)

Game folder: usually `C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert\bin64`, the folder with
`CrimsonDesert.exe`.

1. **ASI loader** (skip it if a mod manager already installed one): download `dinput8-x64.zip` from the
   [Ultimate ASI Loader releases](https://github.com/ThirteenAG/Ultimate-ASI-Loader/releases), put its
   `dinput8.dll` in `bin64` and **rename it to `version.dll`**: the game doesn't load `dinput8.dll`
   (tested 2026-10-23, game 2.03.02).
2. Put `crimsonipelago.asi` in `bin64`.
3. Start the game. The plugin writes `crimsonipelago_plugin.log` next to itself.
4. With the game running, in your `crimsonipelago` folder:
   ```powershell
   py -m crimsonipelago.client.main plugin
   ```
   It prints the game version and the round-trip time. `connect` also reports
   `Game plugin connected` while it runs.

## R1: change your Copper live

With the game running and a save loaded:

```powershell
py -m crimsonipelago.client.main plugin find-copper
```

1. It asks how much money you have.
2. It looks near the item numbers the client gave to stacks it added to your save (900,000,001 and up),
   and searches the whole memory for the amount.
3. It asks you to change your money in the game (buy or sell something). Press Enter instead when nothing
   changed: that drops places that change by themselves. It needs two changes, or one when the amount sits
   next to one of our item numbers.
4. It tries the candidates one by one: adds 100 copper, asks whether the bag shows it, and puts the old
   amount back when it doesn't.

### First attempt (2026-10-23, plugin 0.2.0-r1)

The first amount was typed as `8` and read as 8 copper instead of 8.00 silver, so the search started from the
wrong value; the 3 places left after 5.70 were coincidences (one had moved on by the time it was tried, so
the write was refused and nothing changed). Amounts are now always read as silver, as the bag shows them,
and the client says back how much copper that is.

## Remove

Delete `crimsonipelago.asi` (and the loader DLL if nothing else uses it) from `bin64`. Steam's "Verify
integrity of game files" doesn't remove extra files, but the game never needs them.

## Build

```bash
cmake -S plugin -B build/plugin -A x64       # Visual Studio 2022
cmake --build build/plugin --config Release   # build/plugin/Release/crimsonipelago.asi
```

CI builds it on Windows, loads it into a stand-in `CrimsonDesert.exe`, and checks the client can talk to it;
the `.asi` is attached to every CI run as the `crimsonipelago-plugin` artifact.

## Protocol

Newline-delimited JSON on `127.0.0.1:38301` (localhost only). On connect the plugin sends `hello` with the
game facts; the client sends `{"cmd":"ping","id":N}` or `{"cmd":"info"}`, and for research `scan`,
`rescan`, `read` and `write` (4- or 8-byte values, compare-and-set). See `plugin/src/main.cpp`.
