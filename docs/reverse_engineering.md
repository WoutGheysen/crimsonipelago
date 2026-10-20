# Reverse engineering: Phase 0 and the discovery workflow

This is how real Crimson Desert data gets into the catalogue. Everything here is **read-only**
until step 4 of Phase 0, and even then only on disposable saves.

Current research:

- [research/progression-draft.md](research/progression-draft.md): what the game data says about
  chapters, quests, abilities and items, and a first Archipelago model (unverified).
- [research/first-session-plan.md](research/first-session-plan.md): the checklist for the first
  save-diff session (Prologue and Chapter 1).

## Ground rules

- Use a **dedicated test campaign** in its own save slot. Never experiment on your real campaign.
- Keep backups outside the game's save folder. `crimson-inspect backup` makes read-only, hashed copies.
- Record the game build (Steam build id) with every finding. A game update can change everything.
- A field that changed once is a candidate, not a fact. Every entry needs a second, independent test.
- No live memory editing, no DRM or anti-cheat bypass, no redistributing game files.

## Tools

```bash
crimson-inspect locate                         # list saves found on this PC
crimson-inspect info [save]                    # container header, HMAC check, top-level objects
crimson-inspect backup [save] --label before-quest-x
crimson-inspect snapshot [save] -o before.json
crimson-inspect diff before.json after.json    # also accepts two .save files
crimson-inspect --editor-dir <CrimsonSaveEditor> --fields snapshot ...   # every decoded field
crimson-inspect watch --out research/ --backup research/saves/   # snapshot + diff on every in-game save
```

Without `--editor-dir` the inspector still shows which top-level save objects changed
(`QuestSaveData`, `KnowledgeSaveData`, `InventorySaveData`, …) via content fingerprints. With it,
it shows field-level changes: quest keys and states, mission states, knowledge keys, inventory
items.

## The discovery loop (scope doc 9.3)

The most efficient way to work: start `watch`, then play.

```bash
crimson-inspect --editor-dir ../nattkh/CrimsonSaveEditor watch --out research/ --backup research/saves/
```

For each candidate check or item:

1. **Save** right before the action. `watch` records snapshot A.
2. Do **exactly one** targeted thing: finish one quest, open one chest, learn one ability.
3. **Save** again. `watch` records snapshot B and prints the diff.
4. Write down what you did, the build id and the snapshot file names.
5. Pick the candidate field: the quest key whose state changed, the new knowledge key, the item key.
6. **Repeat** with a second case: the same quest on another test save, or a similar activity.
   The field must behave the same way.
7. Check for false positives. Does the field change from repeatable activities, reloads or travel?
8. Add the catalogue entry with `verified: true` and an `evidence` reference to your notes.
9. Add a regression test with the two snapshots as fixtures.

Diffs always contain noise (play time, position, counters). Doing one action per save pair is what
makes the signal readable.

## Detection kinds

Locations declare how they are detected:

```json
{"detection": {"kind": "quest_state", "quest_key": 123456, "states": [3, 4]}}
{"detection": {"kind": "mission_state", "mission_key": 7890, "states": [2]}}
{"detection": {"kind": "knowledge", "knowledge_key": 4242}}
{"detection": {"kind": "placeholder"}}
```

`states` lists every quest or mission state value that counts as completed. Fill it from what the
diffs actually show, not from guesses.

`placeholder` locations can never be reported by the real adapter, only by the simulated game.
That's why the current catalogue can't produce false checks in a real campaign.

## Phase 0 checklist

The project must not grow into a full randomizer until all of these pass (scope doc 3, acceptance
criteria):

- [ ] **Detection.** At least one quest completion, one discovery and one item pickup identified
      in save data and confirmed twice.
- [ ] **Delivery.** On a disposable save with the game closed: add one inventory item, one
      stackable item, one equipment upgrade and one ability or equivalent. The game loads the
      modified save and the change persists after saving and reloading in-game.
- [ ] **Restore.** A failed or bad write is undone from a backup with no side effects.
- [ ] **Interception (the M6 gate).** For one real progression reward, find out whether it can be
      prevented from being granted, and granted later from outside. The options, in order of
      preference: supported mod hooks, validated game-data overrides, controlled save mutation at
      safe checkpoints, or an alternative progression model.
- [ ] **Documentation.** The game build, findings and limitations are written up here.

If interception proves impossible, the decision gate applies: ship a clearly labelled
Archipelago-compatible **check tracker** (real checks sent, items received as notifications or
safe additive rewards) rather than claiming a full randomizer.

## What to send when asking for help with a diff

- The two `.save` files (or the two snapshots), and the build id.
- One sentence on what happened between them.
- Ideally a second pair for the same kind of action.

## Findings log

| Date | Build | What | Field | Confirmed twice | Notes |
|---|---|---|---|---|---|
| | | | | | |
