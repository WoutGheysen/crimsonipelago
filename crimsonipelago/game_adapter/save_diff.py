"""Compare two snapshots and describe what changed (scope doc 3.3 / 9.3)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .nattkh_backend import NameTables


@dataclass
class SnapshotDiff:
    same_file: bool
    same_profile: bool
    schema_changed: bool
    objects_changed: list[str] = field(default_factory=list)  # "Class#index"
    objects_added: list[str] = field(default_factory=list)
    objects_removed: list[str] = field(default_factory=list)
    quests: list[tuple[int, Any, Any]] = field(default_factory=list)  # key, before, after
    missions: list[tuple[int, Any, Any]] = field(default_factory=list)
    knowledge_added: list[int] = field(default_factory=list)
    knowledge_removed: list[int] = field(default_factory=list)
    items: list[tuple[int, int, int]] = field(default_factory=list)  # key, before, after
    fields: list[tuple[str, str | None, str | None]] = field(default_factory=list)
    deep: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.objects_changed or self.objects_added or self.objects_removed or self.quests
                    or self.missions or self.knowledge_added or self.knowledge_removed or self.items or self.fields)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def _objects(snap: dict) -> dict[str, dict]:
    objs = snap.get("structure", {}).get("objects", [])
    # identity = class name + occurrence number of that class (indices shift when objects are inserted)
    seen: Counter[str] = Counter()
    out = {}
    for obj in objs:
        key = f"{obj['class']}#{seen[obj['class']]}"
        seen[obj["class"]] += 1
        out[key] = obj
    return out


def _keyed_changes(before: dict, after: dict) -> list[tuple[int, Any, Any]]:
    keys = sorted(set(before) | set(after), key=lambda k: int(k))
    return [(int(k), before.get(k), after.get(k)) for k in keys if before.get(k) != after.get(k)]


def diff_snapshots(a: dict, b: dict) -> SnapshotDiff:
    d = SnapshotDiff(
        same_file=a["source"]["sha256"] == b["source"]["sha256"],
        same_profile=a.get("save_identity", {}).get("profile_id") == b.get("save_identity", {}).get("profile_id"),
        schema_changed=a.get("structure", {}).get("schema_sha256") != b.get("structure", {}).get("schema_sha256"),
    )
    if not d.same_profile:
        d.notes.append("snapshots come from different save profiles")
    oa, ob = _objects(a), _objects(b)
    d.objects_removed = sorted(set(oa) - set(ob))
    d.objects_added = sorted(set(ob) - set(oa))
    if d.schema_changed:
        ta = a.get("structure", {}).get("type_count") or 0
        tb = b.get("structure", {}).get("type_count") or 0
        if tb > ta and not d.objects_removed:
            d.notes.append(f"the save gained {tb - ta} new data type(s); normal as the game progresses")
        else:
            d.notes.append("the save's data schema changed in a way progress doesn't explain: "
                           "a game update, or a damaged save")
    d.objects_changed = sorted(k for k in set(oa) & set(ob) if oa[k]["sha256"] != ob[k]["sha256"])

    sa, sb = a.get("state"), b.get("state")
    if sa and sb:
        d.deep = True
        d.quests = _keyed_changes(sa["quests"], sb["quests"])
        d.missions = _keyed_changes(sa["missions"], sb["missions"])
        ka, kb = set(sa["knowledge"]), set(sb["knowledge"])
        d.knowledge_added, d.knowledge_removed = sorted(kb - ka), sorted(ka - kb)
        ia, ib = sa["items"], sb["items"]
        d.items = [(int(k), ia.get(k, 0), ib.get(k, 0))
                   for k in sorted(set(ia) | set(ib), key=int) if ia.get(k, 0) != ib.get(k, 0)]
    elif sa or sb:
        d.notes.append("only one snapshot has decoded state; field-level comparison skipped")

    fa, fb = a.get("fields"), b.get("fields")
    if fa is not None and fb is not None:
        d.fields = [(k, fa.get(k), fb.get(k)) for k in sorted(set(fa) | set(fb)) if fa.get(k) != fb.get(k)]
    return d


QUEST_STATE_LABELS = {
    0: "Unknown", 1: "Locked", 2: "Available", 3: "InProgress",
    4: "CompletionReady", 5: "Completed", 6: "RewardReceived",
}  # as interpreted by the save-editor community; unverified for Crimsonipelago


def _state(entry: Any) -> str:
    if entry is None:
        return "absent"
    s = entry.get("state")
    return f"{s} ({QUEST_STATE_LABELS[s]})" if s in QUEST_STATE_LABELS else str(s)


def render_diff(d: SnapshotDiff, names: NameTables | None = None, *, max_fields: int = 200) -> str:
    names = names or NameTables()
    lines: list[str] = []
    if d.same_file:
        return "The two saves are byte-identical."
    lines += [f"note: {n}" for n in d.notes]
    if d.empty:
        lines.append("No differences in any decoded object (the file differs only in encryption nonce / compression).")
        return "\n".join(lines)
    if d.objects_changed or d.objects_added or d.objects_removed:
        lines.append("Top-level save objects:")
        lines += [f"  changed  {k}" for k in d.objects_changed]
        lines += [f"  added    {k}" for k in d.objects_added]
        lines += [f"  removed  {k}" for k in d.objects_removed]
    if not d.deep:
        lines.append("(Field-level detail needs the save-editor backend: --editor-dir)")
    if d.quests:
        lines.append("Quests:")
        lines += [f"  {names.label('quests', k)}: {_state(x)} -> {_state(y)}" for k, x, y in d.quests]
    if d.missions:
        lines.append("Missions:")
        lines += [f"  {names.label('missions', k)}: {_state(x)} -> {_state(y)}"
                  + (f"  completeCount {x.get('complete_count') if x else None} -> {y.get('complete_count') if y else None}"
                     if (x or {}).get("complete_count") != (y or {}).get("complete_count") else "")
                  for k, x, y in d.missions]
    if d.knowledge_added or d.knowledge_removed:
        lines.append("Knowledge:")
        lines += [f"  + {names.label('knowledge', k)}" for k in d.knowledge_added]
        lines += [f"  - {names.label('knowledge', k)}" for k in d.knowledge_removed]
    if d.items:
        lines.append("Inventory:")
        lines += [f"  {names.label('items', k)}: {x} -> {y} ({y - x:+d})" for k, x, y in d.items]
    if d.fields:
        lines.append(f"Raw fields ({len(d.fields)} changed):")
        for path, x, y in d.fields[:max_fields]:
            lines.append(f"  {path}: {x} -> {y}")
        if len(d.fields) > max_fields:
            lines.append(f"  ... {len(d.fields) - max_fields} more (use --json for all)")
    return "\n".join(lines)
