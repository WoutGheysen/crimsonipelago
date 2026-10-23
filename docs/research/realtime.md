# Real-time play: feasibility and plan

- **Status:** feasible (research, 2026-10-23); step R0 (our own plugin loads and talks to the client) built
- **Today:** items reach the game through the save while the game is closed (proven, tests D and F and the
  first real seed). Real-time means a plugin inside the running game.

## Findings

| Question | Answer |
|---|---|
| Copy protection or anti-cheat in the way? | Only Denuvo **Anti-Tamper** (copy protection, [PCGamesN](https://www.pcgamesn.com/crimson-desert/denuvo-on-steam)); no kernel-level anti-cheat. Third-party mods are not forbidden ([mein-mmo](https://mein-mmo.de/en/crimson-desert-mod-support-what-is-possible,1555333)). |
| Does code run inside the game? | Yes. Native plugins (`.asi`) load through [Ultimate ASI Loader](https://github.com/ThirteenAG/Ultimate-ASI-Loader) from `bin64`, e.g. [Gear Hider](https://www.nexusmods.com/crimsondesert/mods/554) (open source, SafetyHook, pattern scanning). |
| Can it hook game functions? | Yes: [player status modifier](https://github.com/Orcax-1399/CrimsonDesert-player-status-modifier) hooks stat writes and **item gain**; [EnhancedFlight](https://github.com/Ronibuilds/CrimsonDesertMods) changes gliding live; [desert-modding](https://github.com/KiefBC/desert-modding) (Rust) does auto-loot. All use pattern scans and switch themselves off when a patch breaks a signature. |
| Can it talk to another program? | Yes: a [co-op mod](https://github.com/blizz3010/CrimsonDesertCoop) runs Steam P2P networking inside the game and has mapped the player, stats, inventory and item data. |
| Live inventory edits? | Yes: Cheat Engine tables change copper and items live ([vgtimes](https://vgtimes.ru/games/crimson-desert/files/cheats/tables/)). |
| Granting a new item or ability live? | **Not shown publicly yet.** This is the research part. |

## Plan

| Step | What | Proves |
|---|---|---|
| **R0** | `crimsonipelago.asi`: loads, logs the game build, serves the client on `127.0.0.1:38301`. No hooks, no writes. | our code runs in the game and talks to the client |
| R1 | Live change to an item the player already has (Copper) | live writes are safe |
| R2 | A logging plugin that watches the game's reward path while the player picks up items and finishes objectives | where the game grants items and knowledge |
| R3 | Call the game's own reward function: Flight, Abyss Artifacts, new items, live, with the game's own popup; live check detection | real-time Archipelago |

The best target is the game's **reward function**: the one that turned drop set 1014512 into Flight. Calling it
grants anything a drop set can describe (items, knowledge, money) exactly as the story would.

The save path stays as the fallback: when a game patch moves the code the plugin looks for, the plugin turns
itself off and the client delivers through the save as before, so a patch never breaks a seed.
