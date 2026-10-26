"""Developer tool: give draft checks their quest log names, from the save editor's mission_names.json.

    python -m crimsonipelago.tools.name_checks CrimsonSaveEditor/mission_names.json

Draft story checks were named "Mission: step N". The objective's own text (what the quest log says) replaces
"step N": "Reunion: step 2" becomes "Reunion: Learn Nature's Grasp through observation". Regional quest steps
get the same treatment: "Ambition in the Ruins: Search for Leads (step 1)" becomes "Ambition in the Ruins: Talk
to Robert, the overseer". Text markup (`{StaticInfo:…#Shown name}`) becomes the shown name. Names stay unique
within the catalogue, and every rule that names a check (goals) follows the rename.

Only names change: ids and detection don't, so running seeds stay playable (Catalogue.compare_seed), and the
fingerprints are recorded afterwards.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from .draft_slices import DATA
from .record_fingerprints import record

MARKUP = re.compile(r"\{[^{}#]*#([^{}]*)\}")
STEP = re.compile(r"^(?P<head>.+?): step \d+(?: \(\d+\))?$")
REGIONAL_STEP = re.compile(r"^(?P<head>.+?) \(step \d+\)$")


def clean(text: str) -> str:
    text = MARKUP.sub(lambda m: m.group(1), text)
    return re.sub(r"\s+", " ", text).strip().rstrip(".")


def load_names(path: Path) -> dict[int, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw.get("missions", raw) if isinstance(raw, dict) else raw
    return {int(r["key"]): clean(r["display"]) for r in rows if isinstance(r, dict) and r.get("display")}


def new_name(loc: dict, names: dict[int, str]) -> str | None:
    if loc.get("verified"):
        return None                                   # hand-named in a playthrough already
    text = names.get(int(loc.get("detection", {}).get("mission_key", -1)))
    if not text:
        return None
    if loc.get("category") == "quest" and (m := STEP.match(loc["name"])):
        return f"{m.group('head')}: {text}"
    if loc.get("category") == "regional" and (m := REGIONAL_STEP.match(loc["name"])):
        return f"{m.group('head').split(': ')[0]}: {text}"     # the regional quest, then the step
    return None


def _rename_rules(node: Any, renames: dict[str, str]) -> Any:
    if isinstance(node, dict):
        return {k: (renames.get(v, v) if k == "location" and isinstance(v, str) else _rename_rules(v, renames))
                for k, v in node.items()}
    if isinstance(node, list):
        return [_rename_rules(v, renames) for v in node]
    return node


def rename(names: dict[int, str], data: Path = DATA) -> dict[str, str]:
    docs = {n: json.loads((data / f"{n}.json").read_text(encoding="utf-8"))
            for n in ("locations", "regions", "progression")}
    locs = docs["locations"]["locations"]
    taken = {loc["name"] for loc in locs}
    renames: dict[str, str] = {}
    for loc in locs:
        name = new_name(loc, names)
        if name is None or name == loc["name"]:
            continue
        unique, n = name, 2
        while unique in taken:
            unique, n = f"{name} ({n})", n + 1
        taken.discard(loc["name"])
        taken.add(unique)
        renames[loc["name"]] = unique
        loc["name"] = unique
    for loc in locs:
        loc["requires"] = _rename_rules(loc.get("requires"), renames)
    docs["regions"]["connections"] = _rename_rules(docs["regions"]["connections"], renames)
    prog = docs["progression"]
    prog["goals"] = _rename_rules(prog["goals"], renames)
    if "events" in prog:
        prog["events"] = _rename_rules(prog["events"], renames)
    for name, doc in docs.items():
        (data / f"{name}.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    record(data)
    return renames


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("names", type=Path, help="mission_names.json from the save editor")
    parser.add_argument("--data", type=Path, default=DATA)
    args = parser.parse_args(argv)
    renames = rename(load_names(args.names), args.data)
    print(f"renamed {len(renames)} checks")
    for old, new in list(renames.items())[:10]:
        print(f"  {old}  ->  {new}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
