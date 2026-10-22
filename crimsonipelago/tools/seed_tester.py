"""crimson-seeds: bulk seed generation test (scope doc 4 acceptance, 7.2, 10.3).

Runs Archipelago's real generator many times over a set of multiworld
scenarios and reports every failure with its seed number, so a failing seed
can be reproduced exactly with `Generate.py --seed N`.

    crimson-seeds --archipelago PATH [--seeds 25] [--scenario NAME ...] [--jobs 4]

The Crimson Desert APWorld must be installed in that Archipelago checkout
(worlds/crimson_desert, or custom_worlds/crimson_desert.apworld). Partner
games are only used when they exist in that checkout.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

CD = "Crimson Desert"


def cd_player(name: str, **options) -> str:
    opts = {"goal": "first_slice", **options}
    body = "\n".join(f"  {k}: {v}" for k, v in opts.items())
    return f"name: {name}\ngame: {CD}\n{CD}:\n{body}\n"


def other(name: str, game: str) -> str:
    return f"name: {name}\ngame: {game}\n{game}: {{}}\n"


# scenario -> (required partner world folders, player yamls)
SCENARIOS: dict[str, tuple[tuple[str, ...], list[str]]] = {
    "solo-standard": ((), [cd_player("CD1")]),
    "solo-casual": ((), [cd_player("CD1", item_pool_mode="conservative", progression_balancing=75)]),
    "solo-minimal-accessibility": ((), [cd_player("CD1", accessibility="minimal")]),
    "with-checksfinder": (("checksfinder",), [cd_player("CD1"), other("CF", "ChecksFinder")]),
    "with-shorthike": (("shorthike",), [cd_player("CD1"), other("Hiker", "A Short Hike")]),
    "with-timespinner": (("timespinner",), [cd_player("CD1"), other("Lunais", "Timespinner")]),
    "three-crimson-mixed-presets": ((), [
        cd_player("CD1"),
        cd_player("CD2", item_pool_mode="conservative"),
        cd_player("CD3", included_check_categories="['quest', 'boss', 'discovery']"),
    ]),
    "big-mixed": (("checksfinder", "shorthike", "timespinner"), [
        cd_player("CD1"), cd_player("CD2", item_pool_mode="conservative"),
        other("CF", "ChecksFinder"), other("Hiker", "A Short Hike"), other("Lunais", "Timespinner"),
    ]),
}


@dataclass
class Outcome:
    scenario: str
    seed: int
    ok: bool
    seconds: float
    detail: str = ""


def run_one(ap: Path, scenario: str, seed: int, workdir: Path) -> Outcome:
    _, yamls = SCENARIOS[scenario]
    run_dir = workdir / f"{scenario}-{seed}"
    players, out = run_dir / "players", run_dir / "out"
    players.mkdir(parents=True)
    out.mkdir()
    for i, text in enumerate(yamls):
        (players / f"p{i}.yaml").write_text(text, encoding="utf-8")
    env = {**os.environ, "SKIP_REQUIREMENTS_UPDATE": "1", "PYTHONWARNINGS": "ignore"}
    start = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "Generate.py", "--player_files_path", str(players), "--outputpath", str(out),
         "--seed", str(seed), "--spoiler", "0", "--skip_output"],
        cwd=ap, env=env, capture_output=True, text=True, timeout=900)
    took = time.monotonic() - start
    if proc.returncode == 0:
        return Outcome(scenario, seed, True, took)
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-12:]
    return Outcome(scenario, seed, False, took, "\n".join(tail))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="crimson-seeds", description=__doc__.split("\n\n")[0])
    p.add_argument("--archipelago", type=Path, default=os.environ.get("ARCHIPELAGO_PATH"),
                   help="Archipelago source checkout (or set ARCHIPELAGO_PATH)")
    p.add_argument("--seeds", type=int, default=25, help="seeds per scenario")
    p.add_argument("--first-seed", type=int, default=1)
    p.add_argument("--scenario", action="append", choices=sorted(SCENARIOS), help="default: all available")
    p.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    args = p.parse_args(argv)

    ap = args.archipelago
    if not ap or not (Path(ap) / "Generate.py").is_file():
        p.error("--archipelago must point at an Archipelago source checkout")
    ap = Path(ap)
    if not ((ap / "worlds" / "crimson_desert").exists() or (ap / "custom_worlds" / "crimson_desert.apworld").exists()):
        p.error(f"the Crimson Desert APWorld is not installed in {ap}")

    chosen = args.scenario or sorted(SCENARIOS)
    runnable = []
    for name in chosen:
        missing = [w for w in SCENARIOS[name][0] if not (ap / "worlds" / w).is_dir()]
        if missing:
            print(f"skip {name}: partner world(s) {', '.join(missing)} not in this checkout")
        else:
            runnable.append(name)

    seeds = range(args.first_seed, args.first_seed + args.seeds)
    jobs = [(name, s) for name in runnable for s in seeds]
    print(f"generating {len(jobs)} multiworlds ({len(runnable)} scenarios x {args.seeds} seeds, {args.jobs} jobs)")
    with tempfile.TemporaryDirectory(prefix="crimson-seeds-") as tmp, ThreadPoolExecutor(args.jobs) as pool:
        outcomes = list(pool.map(lambda j: run_one(ap, j[0], j[1], Path(tmp)), jobs))

    failures = [o for o in outcomes if not o.ok]
    for name in runnable:
        mine = [o for o in outcomes if o.scenario == name]
        bad = sum(not o.ok for o in mine)
        avg = sum(o.seconds for o in mine) / len(mine)
        print(f"  {'FAIL' if bad else 'ok  '}  {name:<30} {len(mine) - bad}/{len(mine)} passed  avg {avg:.1f}s")
    for o in failures:
        print(f"\n--- {o.scenario} seed {o.seed} failed:\n{o.detail}")
    print(f"\n{len(outcomes) - len(failures)}/{len(outcomes)} seeds generated successfully")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
