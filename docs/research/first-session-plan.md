# First test session: Prologue and Chapter 1

Goal: answer the first Phase 0 questions with real saves. All of this is read-only: `crimson-inspect`
never changes your save.

Background and the keys mentioned here: [progression-draft.md](progression-draft.md).

## Before you start (once)

1. Python 3.12 (tick "Add python.exe to PATH") and Git for Windows installed.
2. In PowerShell:

   ```powershell
   cd $HOME\Documents
   git clone https://github.com/WoutGheysen/crimsonipelago
   git clone --depth 1 https://github.com/NattKh/CRIMSON-DESERT-SAVE-EDITOR-AND-GAME-MODS nattkh
   cd crimsonipelago
   python -m venv .venv
   .venv\Scripts\pip install -e .
   ```

3. Start a **new campaign in a free save slot**. Save once, then run
   `.venv\Scripts\crimson-inspect locate` and copy that slot's `save.save` path.
4. Note your game build: the `buildid` line in
   `C:\Program Files (x86)\Steam\steamapps\appmanifest_3321460.acf`.

## During the session

Start the watcher and leave the window open:

```powershell
.venv\Scripts\crimson-inspect --editor-dir ..\nattkh\CrimsonSaveEditor watch "<save.save path>" --out research --backup research\saves
```

Each time the game writes the save, a snapshot lands in `research\` and the window prints what changed.

**The rule:** one action between two saves. Save (manually if the game allows it, otherwise wait for
the autosave), do the one thing, save again. Write down the time and what you did. Autosaves in
between are fine, since the times keep them apart.

## Checklist

Tick what you manage. Even the first block alone is useful.

### Block 1: Prologue (mission detection)

| ✓ | Action | What we expect to see change |
|---|---|---|
| ☐ | Finish *Unfamiliar Lands* | mission 1000157 state |
| ☐ | Finish *In Ashes* (end of *Ambush*) | mission 1000160, quest 10001 |
| ☐ | Finish *Realm of Uncertainty* | mission 1000620 |
| ☐ | Finish *New Journey* (end of the Prologue) | mission 1000164, quest 1000123 |
| ☐ | Note if a new ability or tool appears (e.g. Blinding Flash on the first Abyss visit) | a new knowledge key |

### Block 2: Chapter 1, *Trials of Kindness* (repeat confirmation)

| ✓ | Action | Expect |
|---|---|---|
| ☐ | Finish any two of its missions, with a save pair around each | missions 1000052 / 1000053 / 1000294 / 1000042 / 1000051 |

This is the "confirmed twice" for the mission-state pattern.

### Block 3: Chapter 1, *Traces* (the important one: abilities)

| ✓ | Action | Expect |
|---|---|---|
| ☐ | *Polar Opposites*, where Axiom Force (the grapple) unlocks | mission 1000048 + a new knowledge key |
| ☐ | *Abyss without Balance* | mission 1000546 |
| ☐ | *Woman in White*, where Flight (the glider) unlocks | mission 1000166, quest 1000509, **knowledge 40002** |

**Keep the save from right after *Woman in White*.** That's the one I'll use for the first write
test: removing Flight on a copy to see whether you can still glide.

### Block 4: one of each, wherever you are

| ✓ | Action | Expect |
|---|---|---|
| ☐ | Open one chest | inventory change |
| ☐ | Discover one new location or Abyss Gate | a knowledge key (map node / `AbyssGate_*`) |
| ☐ | Pick up or earn an **Abyss Artifact** (item 75001) | inventory count |
| ☐ | Spend one Abyss Artifact on a skill, if you have one | count down + a new ability key |
| ☐ | If you find one: solve a **Sealed Abyss Artifact** challenge | item or flag change |

## Afterwards

1. Stop the watcher (Ctrl+C).
2. Zip the whole `research` folder (snapshots and save copies).
3. Send me the zip, your notes and the build id.

Your notes can be this simple:

```text
21:05  saved, then finished "Unfamiliar Lands", saved
21:12  opened a chest near the camp
21:30  Woman in White done, got the glider
```

## What happens next

From the zip, I will:

1. Confirm which state values mean completed for missions and quests, and whether a separate
   reward-received step exists.
2. Turn every confirmed mission, quest and ability into real catalogue entries.
3. Prepare two **test copies** of your post-*Woman in White* save:
   - one with Flight (40002) removed: can you still glide?
   - one with one extra Abyss Artifact: does it stay after saving and reloading?
4. You load each copy (from a spare slot) and tell me what happened. Those two answers decide
   most of Phase 0.
