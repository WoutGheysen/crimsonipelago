# Hosting a multiworld with Crimson Desert

For whoever generates and hosts the seed. Crimson Desert is a **custom world**, so the website can't generate it.
You generate the seed on your own PC, then host the result yourself or upload it to archipelago.gg.

## What you need

- [Archipelago](https://github.com/ArchipelagoMW/Archipelago/releases) **0.6.7 or 0.6.8**, the versions it's
  tested on.
- From the Crimson Desert player:
  - `crimson_desert.apworld`, the world;
  - their player options file (`.yaml`).
- Everyone else's `.yaml` files, as usual.

You don't need Crimson Desert, its client, or anything else from Crimsonipelago.

## Steps

1. **Install the world:** double-click `crimson_desert.apworld`, or copy it into Archipelago's `custom_worlds`
   folder. Restart the Archipelago Launcher if it was open.
2. **Gather the options files** in Archipelago's `Players` folder: the Crimson Desert one and everyone else's.
3. **Generate:** in the Launcher, *Generate* (or run `ArchipelagoGenerate.exe`). The seed lands in the `output`
   folder as `AP_<number>.zip`. If generation fails on a Crimson Desert option, the message says which one and why.
4. **Host:**
   - **On archipelago.gg:** upload the zip at <https://archipelago.gg/uploads> and create a room. Players connect
     to the address the room shows (`archipelago.gg:<port>`).
   - **On your PC:** open the zip with *Host* in the Launcher (or `ArchipelagoServer.exe`). Players outside your
     network need port 38281 forwarded.
5. Give the Crimson Desert player the address, their slot name, and the password, if there is one.

## Good to know about Crimson Desert in a multiworld

- **It's a long game.** A whole-game seed (`goal: journeys_end`) covers the entire story, 409 checks, and many
  more with the optional categories. Shorter goals end at a chapter: `chapter_2` is about 90 checks.
- **Checks send in bursts.** The game reports what's done when it saves, so items for other players arrive a few
  at a time after each autosave, not one by one.
- **Most of the world is still a draft.** Prologue and chapter 1 are confirmed in play. The later chapters come
  straight from the game's data and are being confirmed as people play. A draft check might not send.
  - The option `draft_checks_keep_others_safe` (**on by default**) keeps other players' progression items out of
    draft checks, so a check that doesn't send never leaves anyone stuck. Leave it on.
  - If something does get stuck, `/send <player> <item>` in the server console gives it.
    `/hint <player> <item>` shows where it is.
- **Release and collect:** the usual room settings work. With a long Crimson Desert seed, `release_mode: goal` lets
  others finish without waiting for the whole story.

## For the Crimson Desert player

1. **Build the world file** for the host, from your `crimsonipelago` folder:
   `py -m crimsonipelago.tools.build_apworld -o crimson_desert.apworld`.
2. **Your options file:** start from `examples/player.yaml`, set `name`, and choose a `goal`. The whole game is
   `journeys_end` with `include_draft_slices: true`.
3. **Before you play that seed,** install the game-data override that matches it. The client names it when it
   connects (`docs/first_seed.md`, step 5).
4. **This is a new seed:** start a new game for it. A solo seed you were playing stays bound to its own playthrough.
