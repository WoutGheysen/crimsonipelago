# Your first real seed (Windows, from the repo)

This plays the first slice for real: 42 checks from the prologue to the woman in white, with Flight,
3 Abyss Artifacts and Copper in the item pool. Solo is enough for a first run. A friend with another game
can join the same seed.

**Before you start:**
- Merge the latest pull request.
- Keep Steam Cloud **off**.
- Have about an hour.

## 1. Update your copy (PowerShell, in your `crimsonipelago` folder)

```powershell
git checkout main
git pull
pip install -e .
crimson-build-apworld
```

That last command writes `dist\crimson_desert.apworld`.

## 2. Install the world into Archipelago

- No Archipelago yet? Install it from https://github.com/ArchipelagoMW/Archipelago/releases (0.6.7 or newer).
- Double-click `dist\crimson_desert.apworld`, or copy it into Archipelago's `custom_worlds` folder.

## 3. Make your player file and generate

1. Copy `examples\player.yaml` into Archipelago's `Players` folder and name it `Wout.yaml`.
2. Inside the file, change the first line to `name: Wout`.
3. Remove any other `.yaml` from `Players`, unless a friend is joining.
4. Open the Archipelago Launcher and click **Generate**. A file `AP_….zip` appears in Archipelago's `output` folder.

## 4. Host it

- In the Launcher, click **Host** and pick that zip. The server runs on your PC at `localhost:38281`.
- Leave its window open while you play.
- To play with friends over the internet, upload the zip at https://archipelago.gg/uploads instead and use the address it shows.

## 5. Install the game-data override (game closed)

```powershell
crimson-mod status
crimson-mod install flight-grant-removed     # only if status says nothing is installed
```

Without it, the story still hands you Flight at the woman in white, and the client warns you about that.

## 6. Start the client (PowerShell, in your `crimsonipelago` folder)

```powershell
crimsonipelago connect --server localhost:38281 --slot Wout --editor-dir "C:\path\to\CrimsonSaveEditor"
```

- `--editor-dir` is the same CrimsonSaveEditor folder you used for `crimson-inspect`.
- After the first time, `crimsonipelago connect` alone is enough: the client remembers the settings.
- Always start it from this folder. It keeps its records in `crimsonipelago_data` here.
- Leave the window open while you play.

## 7. Play

1. Start Crimson Desert and begin a **new game**.
2. For the first 15 minutes or so, the client says it is *waiting for the campaign*. A new game looks the same for everyone until then. That's normal: nothing is lost, it catches up.
3. **Checks are sent when the game saves.** Autosaves are about every 15 minutes. A manual save sends them right away. The client prints `Sent check: …` for each one.
4. **Items are added while the game is closed.** When the client says you received something:
   - Quit to desktop. The client prints `Delivered: …`.
   - Start the game and load the newest save. Flight lets you glide; Abyss Artifacts and Copper are in your bag.
   - Crow Feathers do nothing in the game and count as received straight away.
5. The goal is **speaking with the woman in white**. The client reports victory to the server.

## What to tell me afterwards

Anything odd, with the client's lines from around that moment. Especially:

- Did each check you completed show up as `Sent check`? Which ones didn't?
- Did Flight and the artifacts arrive after quitting to desktop?
- Anything confusing in what the client printed.

Afterwards, run `crimsonipelago status`, and zip the `crimsonipelago_data` folder if I should look at something.
