# The game plugin (R0)

`crimsonipelago.asi` runs inside Crimson Desert and lets the client talk to the running game. R0 only proves
the link: it touches nothing in the game (no hooks, no memory writes).

## Install (Windows)

Game folder: usually `C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert\bin64`, the folder with
`CrimsonDesert.exe`.

1. **ASI loader** (skip it if a mod manager already installed one): download `dinput8-x64.zip` from the
   [Ultimate ASI Loader releases](https://github.com/ThirteenAG/Ultimate-ASI-Loader/releases) and put its
   `dinput8.dll` in `bin64`. If the plugin doesn't load, rename it to `winmm.dll`.
2. Put `crimsonipelago.asi` in `bin64`.
3. Start the game. The plugin writes `crimsonipelago_plugin.log` next to itself.
4. With the game running, in your `crimsonipelago` folder:
   ```powershell
   py -m crimsonipelago.client.main plugin
   ```
   It prints the game version and the round-trip time. `connect` also reports
   `Game plugin connected` while it runs.

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
game facts; the client sends `{"cmd":"ping","id":N}` or `{"cmd":"info"}`. See `plugin/src/main.cpp`.
