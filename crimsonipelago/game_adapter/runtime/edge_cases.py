"""Phase 1: what live delivery does around menus, the horse, fast travel, loading a save, dying and the main menu.

A guided test on a test playthrough. Around each step it gives 1 silver (100 copper) the way the client does, and
asks the player what happened to it. All the while it records every use of the game's own "add" (which
transaction, whose, on which thread, the game's clock), so the report shows what a reload or a death looks like
from the plugin's side. That is what the client needs to notice a reload on its own: an item given live and lost
to a reload before the game saved must be given again (docs/research/phase1-edge-cases.md).

The report is research/r6-edges-*.json.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .bridge import BridgeError, ResearchBridge
from .live import LiveChannel, LiveResult, wait_for_transaction
from .research import TARGETS_FILE

COPPER_KEY = 1
SILVER = 100              # copper
GIVE_WINDOW = 60.0        # seconds to keep trying a delivery after a step
DURING_WINDOW = 15.0      # seconds to try while a menu is open
KEEP_SIGHTINGS = 200      # raw sightings kept per step in the report


@dataclass(frozen=True)
class Step:
    name: str
    do: str                                  # what the player does
    before: bool = False                     # give 1 silver first, to see whether it survives the step
    during: bool = False                     # try a delivery while the step is going on (a menu is open)
    questions: tuple[str, ...] = field(default=())


STEPS = (
    Step("standing", "Stand somewhere quiet in the open world."),
    Step("menu", "Open the map and leave it open until I say so.", during=True,
         questions=("Did the silver from just now arrive while the map was open (not only after closing it)?",)),
    Step("horse", "Call your horse, get on and ride for a few seconds. Stay on it."),
    Step("fast_travel", "Fast travel somewhere (get off the horse first if you have to), and wait until you can "
                        "move again."),
    Step("reload", "Don't save. Open the menu and load the manual save you made at the start, then wait until "
                   "you can move again.", before=True,
         questions=("Is your money the same as just before loading (the silver from just before is still there)?",)),
    Step("death", "Die: fall from somewhere high, or let an enemy win. Wait until you can move again.", before=True,
         questions=("Is your money the same as just before dying (the silver from just before is still there)?",
                    "Was there a loading screen after dying?",
                    "Are you back where you died (not at a checkpoint or camp)?")),
    Step("main_menu", "Quit to the main menu, load your manual save again, and wait until you can move again.",
         before=True,
         questions=("Is your money the same as before quitting (the silver from just before is still there)?",)),
)


def _yes(answer: str) -> bool:
    return answer.strip().lower().startswith(("y", "j"))


def give_silver(live: LiveChannel, window: float, clock: Callable[[], float], wait: Callable[[float], None]) -> dict:
    """Give 1 silver, trying again while the game isn't ready, for at most `window` seconds."""
    start, tries = clock(), 0
    result = LiveResult("not now", "not tried")
    while True:
        tries += 1
        result = live.give(COPPER_KEY, SILVER)
        if result.state != "not now" or clock() - start >= window:
            break
        wait(1.0)
    job = result.detail.get("job") or {}
    return {"state": result.state, "message": result.message, "code": result.code, "tries": tries,
            "seconds": round(clock() - start, 1), "waited_ms": job.get("waited_ms"),
            "caller": result.detail.get("transaction_last_used_by")}


def summarize(sightings: list[dict]) -> dict:
    """What changed in the game's transaction during a step."""
    if not sightings:
        return {"count": 0}
    clocks = [s["clock"] for s in sightings]
    went_back = sum(1 for a, b in zip(clocks, clocks[1:]) if b < a)
    return {"count": len(sightings), "tx": sorted({s["tx"] for s in sightings}),
            "owners": sorted({s["owner"] for s in sightings}), "threads": sorted({s["tid"] for s in sightings}),
            "callers": sorted({s["caller"] for s in sightings}), "clock_first": clocks[0], "clock_last": clocks[-1],
            "clock_went_back": went_back, "gap_longest": round(max((b["t"] - a["t"] for a, b in
                                                                     zip(sightings, sightings[1:])), default=0.0), 1)}


class _Watching:
    """Keep reading the game's transaction in the background while the player is busy (input() blocks)."""

    def __init__(self, live: LiveChannel, enabled: bool):
        self.live, self.enabled = live, enabled
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    def __enter__(self):
        if self.enabled:
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
        return self

    def _run(self) -> None:
        while not self.stop.is_set():
            self.live.poll()
            self.stop.wait(0.5)

    def __exit__(self, *exc):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=5)


def edge_cases(bridge: ResearchBridge, report_dir: Path, ask: Callable[[str], str] = input,
               say: Callable[[str], None] = print, wait: Callable[[float], None] = time.sleep,
               clock: Callable[[], float] = time.monotonic, targets_file: Path = TARGETS_FILE,
               steps: tuple[Step, ...] = STEPS, background: bool = True) -> Path | None:
    live = LiveChannel(on_event=say, targets_file=targets_file, clock=clock, sleep=wait)

    def asking(prompt: str) -> str:
        with _Watching(live, background):
            return ask(prompt)

    say("Phase 1 edge cases: what happens to items given while you play, around menus, the horse, fast travel, "
        "loading a save, dying and the main menu. About 10 minutes; you get about 10 silver in total.")
    say("Use your test playthrough (not the seed you'll play). Make a manual save now: step 5 loads it.")
    if not _yes(ask("Saved, and standing in the open world (not in a menu)? (y/n) ")):
        return None
    if not live.attach(bridge):
        return None
    info = bridge.info
    report: dict = {"plugin": info.plugin_version if info else None,
                    "game": {"exe_version": info.exe_version, "pe_timestamp": info.pe_timestamp} if info else None,
                    "steps": []}
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"r6-edges-{time.strftime('%Y%m%d-%H%M%S')}.json"

    def save(outcome: str) -> Path:
        report["outcome"] = outcome
        path.write_text(json.dumps(report, indent=1), encoding="utf-8")
        return path

    try:
        say("Waiting for the game to use its own transaction (walk around, or pick something up)...")
        if not wait_for_transaction(live):
            say("The game didn't use it within 90 seconds. Run this again and pick something up while it waits.")
            return save("no transaction seen")
        for n, step in enumerate(steps, 1):
            say("")
            say(f"Step {n}/{len(steps)}: {step.name.replace('_', ' ')}")
            mark = live.clock()
            entry: dict = {"step": step.name}
            if step.before:
                say("   Sending 1 silver first...")
                entry["before"] = give_silver(live, GIVE_WINDOW, clock, wait)
                if entry["before"]["state"] == "given":
                    entry["before"]["arrived"] = _yes(asking("   Did 1 silver arrive? Remember how much money you "
                                                             "have now. (y/n) "))
                else:
                    say(f"   Not given: {entry['before']['message']}")
            say(f"   {step.do}")
            if step.during:
                asking("   Press Enter once it's open. ")
                say("   Sending 1 silver while it's open...")
                entry["during"] = give_silver(live, DURING_WINDOW, clock, wait)
                say(f"   {entry['during']['state']}" + (f": {entry['during']['message']}"
                                                         if entry["during"]["message"] else ""))
                say("   Now close it and go back to the game.")
            asking("   Press Enter when you're done and can move again. ")
            done = live.clock()
            entry["answers"] = {q: _yes(asking(f"   {q} (y/n) ")) for q in step.questions}
            say("   Sending 1 silver now...")
            entry["after"] = give_silver(live, GIVE_WINDOW, clock, wait)
            if entry["after"]["state"] == "given":
                entry["after"]["arrived"] = _yes(asking(f"   Given after {entry['after']['seconds']:.0f} s. "
                                                        "Did 1 silver arrive? (y/n) "))
            else:
                say(f"   Not given: {entry['after']['message']}")
            seen = [s for s in live.sightings if s["t"] >= mark]
            entry["seconds"] = round(done - mark, 1)
            entry["transaction"] = summarize(seen)
            entry["sightings"] = seen[-KEEP_SIGHTINGS:]
            report["steps"].append(entry)
            save("in progress")
            changed = [k for k in ("tx", "owners", "threads") if len(entry["transaction"].get(k, [])) > 1]
            if changed or entry["transaction"].get("clock_went_back"):
                say("   (the game's transaction changed during this step: " +
                    ", ".join(changed + (["its clock went back"] if entry["transaction"].get("clock_went_back")
                                         else [])) + ")")
    except (KeyboardInterrupt, EOFError):
        say("Stopped. What was recorded so far is kept.")
        save("stopped")
        say(f"Saved: {path}")
        return path
    except BridgeError as exc:
        say(f"Lost the game plugin ({exc}). What was recorded so far is kept.")
        save(f"plugin lost: {exc}")
        say(f"Saved: {path}")
        return path
    finally:
        try:
            bridge.unwatch()          # the bridge is the caller's: leave it open
        except BridgeError:
            pass
    save("done")
    say("")
    say("Done. Per step:")
    for entry in report["steps"]:
        after = entry["after"]
        say(f"   {entry['step']:<12} delivery after it: {after['state']} in {after['seconds']:.0f} s"
            + "".join(f"; {q.split('(')[0].strip()} {'yes' if a else 'no'}" for q, a in entry["answers"].items()))
    say(f"Saved: {path}")
    say("Send me that file. You can load your manual save again to undo the silver.")
    return path
