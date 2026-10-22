"""Recognise which playthrough ("campaign") a save belongs to, whatever slot it is in.

The game rotates autosaves over slot0-2 and keeps manual saves in slot100+, so the slot says nothing
about which playthrough a save is from, and the save has no campaign id of its own (checked on every
save collected so far). What it does have is history that only ever grows and is unique to one
playthrough: when each quest was completed and when each knowledge entry was learned, in game time.

Anchors are those (key, time) pairs. Times shared by three or more entries in one save are dropped:
those are scripted (the opening completes a batch of quests at the same moment in every playthrough).
On the saves collected so far, two campaigns share no anchors at all, and every later save of a
campaign keeps the anchors of its earlier saves.

A campaign is registered the first time a save with enough anchors is seen; it is remembered by the
anchors it had then. A save belongs to that campaign when it still has at least 80% of them. Saves
with too little history are not assigned to anything yet (the player just started).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .nattkh_backend import DeepState

MIN_ANCHORS = 5          # a campaign is only registered once its saves are this distinctive
MATCH_FRACTION = 0.8     # share of a campaign's anchors a save must still have
SCRIPTED_GROUP = 3       # this many entries with the same time = scripted, not a fingerprint

Anchor = tuple[str, int, int]


def anchors(state: DeepState) -> set[Anchor]:
    raw: list[Anchor] = []
    for key, q in state.quests.items():
        t = q.get("completed_time")
        if t:
            raw.append(("q", int(key), int(t)))
    for key, t in state.knowledge_times.items():
        if t:
            raw.append(("k", int(key), int(t)))
    counts = Counter(t for _, _, t in raw)
    return {a for a in raw if counts[a[2]] < SCRIPTED_GROUP}


def _encode(a: Anchor) -> str:
    return f"{a[0]}:{a[1]}:{a[2]}"


def _decode(s: str) -> Anchor:
    kind, key, t = s.split(":")
    return kind, int(key), int(t)


@dataclass
class Campaign:
    id: str
    account: str
    anchors: frozenset[Anchor]
    first_seen: str = ""

    def matches(self, save_anchors: set[Anchor]) -> float:
        """Share of this campaign's anchors the save still has. An older save of the campaign, from before
        it was registered, has fewer anchors but all of them ours: that counts as a full match too."""
        if not self.anchors:
            return 0.0
        if len(save_anchors) >= MIN_ANCHORS and save_anchors <= self.anchors:
            return 1.0
        return len(self.anchors & save_anchors) / len(self.anchors)


@dataclass(frozen=True)
class Identification:
    campaign: Campaign | None
    problem: str | None = None


class CampaignRegistry:
    """Campaigns seen by this client, stored as JSON next to the ledger."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.campaigns: dict[str, Campaign] = {}
        self._load()

    def _load(self) -> None:
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for c in doc.get("campaigns", []):
            self.campaigns[c["id"]] = Campaign(c["id"], c.get("account", ""),
                                               frozenset(_decode(a) for a in c["anchors"]), c.get("first_seen", ""))

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        doc = {"version": 1, "campaigns": [
            {"id": c.id, "account": c.account, "first_seen": c.first_seen, "anchors": sorted(_encode(a) for a in c.anchors)}
            for c in self.campaigns.values()]}
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def identify(self, state: DeepState, account: str, *, register: bool = True, now: str = "") -> Identification:
        found = anchors(state)
        scored = sorted(((c.matches(found), c) for c in self.campaigns.values() if c.account == account),
                        key=lambda x: x[0], reverse=True)
        if scored and scored[0][0] >= MATCH_FRACTION:
            return Identification(scored[0][1])
        if len(found) < MIN_ANCHORS:
            return Identification(None, "this save does not have enough history yet to tell which playthrough it "
                                        "is; keep playing until the next save (about 15 minutes in)")
        if not register:
            return Identification(None, "this save is from a playthrough the client has not seen before")
        digest = hashlib.sha256("|".join(sorted(_encode(a) for a in found)).encode()).hexdigest()[:10]
        campaign = Campaign(f"{account}/campaign-{digest}", account, frozenset(found), now)
        self.campaigns[campaign.id] = campaign
        self._save()
        return Identification(campaign)
