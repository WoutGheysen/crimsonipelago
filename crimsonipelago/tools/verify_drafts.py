"""Developer tool: confirm draft checks, goals and items from a playthrough's live logs.

    python -m crimsonipelago.tools.verify_drafts live-20261025.jsonl [more.jsonl ...]           # report
    python -m crimsonipelago.tools.verify_drafts logs/*.jsonl --write                           # and mark them

A seed with draft chapters logs each draft check the first time the client sees it complete in a save
(`draft_check`), the goal likewise (`draft_goal`), and every live delivery (`give`). That's the evidence a draft
needs, so a normal playthrough confirms the drafts as it goes, without test runs.

- A **check** seen completing is marked verified.
- A **goal** is marked verified once it was seen and every story check its seeds hold is verified (the catalogue
  doesn't allow a verified goal over unverified checks; optional ones are left out of seeds without drafts).
  Checks of a finished chapter that were never seen are listed: optional or missable steps, or a detection to fix.
- A draft **item** the game accepted live is listed, not marked: whether it did what it should (Double Jump jumps,
  Vitality adds health) is the player's word.
- Checks done **before** a progression item they're gated on arrived show where logic can be relaxed.

`--write` also records the new seed fingerprints (tools/record_fingerprints), so running seeds stay playable.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from crimson_desert import catalogue as cat_mod

from .draft_slices import DATA
from .live_log import read
from .record_fingerprints import record

EVIDENCE = "docs/research/drafts.md (seen completing in play: {log} {day})"


def _items_in(rule) -> set[str]:
    if isinstance(rule, dict):
        if "item" in rule:
            return {rule["item"]}
        return set().union(*(_items_in(r) for r in rule.get("all", []) + rule.get("any", []))) if (
            rule.get("all") or rule.get("any")) else set()
    return set()


def region_gates(raw: dict) -> dict[str, set[str]]:
    """Region -> items its checks need (every connection on the way there, from the raw catalogue)."""
    into = {c["to"]: c for c in raw["regions"].get("connections", [])}
    out: dict[str, set[str]] = {}
    for region in (r["name"] for r in raw["regions"].get("regions", [])):
        needs: set[str] = set()
        at, seen = region, set()
        while at in into and at not in seen:
            seen.add(at)
            needs |= _items_in(into[at].get("requires"))
            at = into[at]["from"]
        out[region] = needs
    return out


def analyse(rows: list[dict], cat) -> dict:
    by_id = {loc.ap_id: loc for loc in cat.locations.values()}
    seen_checks: dict[int, dict] = {}
    seen_goals: dict[str, dict] = {}
    for r in rows:
        if r.get("event") == "draft_check" and r.get("ap_id") in by_id:
            seen_checks.setdefault(int(r["ap_id"]), r)
        elif r.get("event") == "draft_goal" and r.get("goal") in cat.goals:
            seen_goals.setdefault(r["goal"], r)

    by_ref: dict[tuple[str, int], str] = {}
    for item in cat.items.values():
        ref = dict(item.game_ref or {})
        if ref.get("kind") == "knowledge":
            by_ref[("knowledge", int(ref["knowledge_key"]))] = item.name
        elif ref.get("kind") == "inventory_item":
            by_ref[("item", int(ref["item_key"]))] = item.name
    arrived: dict[str, str] = {}
    for r in rows:
        if r.get("event") == "give" and r.get("state") == "given":
            name = by_ref.get((r.get("what"), int(r.get("key", -1))))
            if name:
                arrived.setdefault(name, r["at"])
    items = {name: at for name, at in arrived.items() if not cat.items[name].verified}

    gates = region_gates(cat.raw)
    early: list[str] = []
    for ap_id, r in seen_checks.items():
        loc = by_id[ap_id]
        for item in sorted(gates.get(loc.region, ())):
            if item not in arrived or r["at"] < arrived[item]:
                early.append(f"{loc.name} ({loc.region}) done without {item}")

    goals = {}
    for key, r in seen_goals.items():
        missing = [loc.name for loc in cat.locations_in_scope(key)
                   if not loc.verified and loc.ap_id not in seen_checks and not loc.no_progression
                   and cat.region_slices.get(loc.region, 1) == cat.goal_slice(key)]
        goals[key] = {"at": r["at"], "unseen": missing}
    return {"checks": seen_checks, "goals": goals, "items": items, "early": early}


def write(result: dict, data: Path) -> dict[str, int]:
    docs = {n: json.loads((data / f"{n}.json").read_text(encoding="utf-8")) for n in ("locations", "progression")}
    marked = {"checks": 0, "goals": 0}
    for loc in docs["locations"]["locations"]:
        r = result["checks"].get(loc["ap_id"])
        if r is not None and not loc.get("verified"):
            loc["verified"] = True
            loc["evidence"] = EVIDENCE.format(log=r.get("_log", "live log"), day=r["at"][:10])
            marked["checks"] += 1
    for name, doc in docs.items():
        (data / f"{name}.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # goals: only once every check their seeds hold is verified
    cat = cat_mod.load(data, validate=False)
    for key in result["goals"]:
        goal = docs["progression"]["goals"][key]
        if goal.get("verified"):
            continue
        if all(loc.verified or loc.no_progression for loc in cat.locations_in_scope(key)):
            goal["verified"] = True
            marked["goals"] += 1
    (data / "progression.json").write_text(json.dumps(docs["progression"], indent=2, ensure_ascii=False) + "\n",
                                           encoding="utf-8")
    problems = cat_mod.validate_catalogue(cat_mod.load(data, validate=False))
    if problems:
        raise SystemExit("the catalogue doesn't validate after marking:\n  " + "\n  ".join(problems))
    record(data)
    return marked


def report(result: dict, cat) -> list[str]:
    out = []
    per_slice: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for loc in cat.locations.values():
        if loc.verified:
            continue
        s = cat.region_slices.get(loc.region, 1)
        per_slice[s][1] += 1
        if loc.ap_id in result["checks"]:
            per_slice[s][0] += 1
    out.append("draft checks seen completing, per chapter (slice):")
    for s in sorted(per_slice):
        seen, total = per_slice[s]
        if seen:
            out.append(f"  {s:>2}: {seen}/{total}")
    if not any(seen for seen, _ in per_slice.values()):
        out.append("  none yet")
    for key, info in result["goals"].items():
        out.append(f"goal {key} seen {info['at'][:19]}; story checks of that chapter never seen: "
                   f"{len(info['unseen'])}")
        out += [f"    {name}" for name in info["unseen"][:40]]
    if result["items"]:
        out.append("draft items the game accepted live (confirm they work, then mark them by hand):")
        out += [f"  {name} first at {at[:19]}" for name, at in result["items"].items()]
    if result["early"]:
        out.append("checks done before an item their logic needs had arrived (logic can be relaxed there):")
        out += [f"  {line}" for line in result["early"][:60]]
    return out


def expand(paths: list[Path]) -> list[Path]:
    """Files as given; a folder means its live-*.jsonl; a pattern is expanded here (PowerShell passes it as is)."""
    out: list[Path] = []
    for path in paths:
        if path.is_dir():
            out += sorted(path.glob("live-*.jsonl"))
        elif any(c in path.name for c in "*?["):
            out += sorted(path.parent.glob(path.name))
        else:
            out.append(path)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("logs", type=Path, nargs="+", help="log files, a logs folder, or a pattern like live-*.jsonl")
    parser.add_argument("--write", action="store_true", help="mark what was seen as verified in the catalogue")
    parser.add_argument("--data", type=Path, default=DATA)
    args = parser.parse_args(argv)
    rows = []
    for path in expand(args.logs):
        for row in read([path]):
            row["_log"] = path.name
            rows.append(row)
    rows.sort(key=lambda r: r.get("at", ""))
    cat = cat_mod.load(args.data)
    result = analyse(rows, cat)
    print("\n".join(report(result, cat)))
    if args.write:
        marked = write(result, args.data)
        print(f"marked {marked['checks']} checks and {marked['goals']} goals verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
