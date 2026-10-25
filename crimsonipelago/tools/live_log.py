"""Read the client's live delivery logs (crimsonipelago_data/logs/live-*.jsonl): what was delivered, refused,
lost to a load, and, for Phase 2, what the game's own transactions changed, matched against the catalogue's checks.

    python -m crimsonipelago.tools.live_log live-20261025.jsonl [more.jsonl ...]

The match is the Phase 2 question: when a quest, mission or knowledge check completes, does a transaction carry
its key? Each change entry's two numbers (+8 and +0xC) are looked up among the keys the catalogue's checks are
detected by.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def read(paths: list[Path]) -> list[dict]:
    rows = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return sorted(rows, key=lambda r: r.get("at", ""))


def check_keys(catalogue) -> dict[int, list[str]]:
    """Every key a check is detected by -> 'kind name' of those checks."""
    keys: dict[int, list[str]] = defaultdict(list)
    for loc in catalogue.locations.values():
        det = dict(loc.detection or {})
        for field in ("quest_key", "mission_key", "knowledge_key", "parent_mission_key"):
            if det.get(field) is not None:
                keys[int(det[field])].append(f"{field.split('_')[0]} {loc.name}")
    return keys


def summarize(rows: list[dict], keys: dict[int, list[str]]) -> list[str]:
    out = []
    events = Counter(r.get("event") for r in rows)
    out.append(f"{len(rows)} lines: " + ", ".join(f"{k} {v}" for k, v in events.most_common()))
    gives = Counter((r.get("what"), r.get("state")) for r in rows if r.get("event") == "give")
    out.append("deliveries: " + ", ".join(f"{w} {s} {n}" for (w, s), n in sorted(gives.items(), key=str)))
    codes = Counter(hex(r["code"]) for r in rows if r.get("event") == "give" and r.get("code"))
    if codes:
        out.append("result codes: " + ", ".join(f"{c} x{n}" for c, n in codes.most_common()))
    for r in rows:
        if r.get("event") in ("load", "signatures", "connected", "disconnected"):
            detail = {k: v for k, v in r.items() if k in ("loads", "owner", "owner_before", "clock", "clock_before",
                                                           "build", "plugin", "why", "found", "problems")}
            out.append(f"  {r['at'][:19]} {r['event']} {json.dumps(detail)}")
    kinds: Counter = Counter()
    samples: dict[int, set] = defaultdict(set)
    matched: list[str] = []
    for r in rows:
        if r.get("event") != "seen":
            continue
        for side in ("adds", "removes"):
            for e in r.get(side, []):
                if "kind" not in e:
                    continue
                kinds[(side, e["kind"])] += 1
                if len(samples[e["kind"]]) < 6:
                    samples[e["kind"]].add((e["a"], e["b"]))
                for value in (e["a"], e["b"]):
                    for check in keys.get(value, []):
                        matched.append(f"  {r['at'][:19]} {side} kind {e['kind']}: {value} = {check}")
    if kinds:
        out.append("the game's own transactions, entries by kind:")
        for (side, kind), n in sorted(kinds.items()):
            out.append(f"  {side:<7} kind {kind:>2}: {n:>5}   e.g. {sorted(samples[kind])[:6]}")
    out.append(f"entries carrying a check's key: {len(matched)}")
    out += matched[:200]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("logs", nargs="+", type=Path)
    args = parser.parse_args(argv)
    from crimson_desert.catalogue import default_catalogue

    print("\n".join(summarize(read(args.logs), check_keys(default_catalogue()))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
