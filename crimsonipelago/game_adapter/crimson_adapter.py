"""The real Crimson Desert adapter: save-file based (the scope doc's fallback path).

Reading: quest, mission and knowledge state of the newest save, through the editor's decoder.

Identity: the newest save is assigned to a campaign (playthrough) by its history, so autosave
rotation and manual slots all map to the same campaign (see campaign.py).

Writing: only item kinds with a transformation verified in-game are delivered; everything else
reports UNSUPPORTED and stays parked. Verified so far:

    knowledge       add the key to both knowledge lists, as the story does (test D, 2026-10-22:
                    Flight given this way works, and survives the game's own save and a restart)
    inventory_item  add to the stack in the main bag, or a new stack (save_edit.give_items; test F,
                    2026-10-23: Copper, a pouch and Abyss Artifacts show, work and survive a restart)

An item that can't go in right now (a full stack, say) is left out of the write and stays pending with
the reason; the rest of the batch is delivered.

Every write goes through the same gate: supported game version, game closed, a backup, the new save
built in a temp file and decoded again, then an atomic swap (two-phase, see interface.py).
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from crimson_desert.catalogue import Catalogue, ItemDef, LocationDef

from . import parc, save_container, save_edit
from .campaign import CampaignRegistry
from .interface import DeliveryBatch, DeliveryEntry, GameAdapter, GameVersion, PreparedWrite, Result, SaveIdentity
from .nattkh_backend import BackendUnavailable, DeepState, NattKhBackend
from .process_check import game_running
from .save_locator import backup_save, find_saves, slot_from_path
from .version_detection import detect_version

BlobTransform = Callable[[bytes, list[DeliveryEntry]], bytes]

# the game-data overrides: which one a seed needs (re-exported: tests and older callers import them from here)
from .overrides import (  # noqa: E402,F401
    OVERRIDE_REMOVES,
    REQUIRED_OVERRIDES,
    advise,
    best_override,
    story_items_of,
)


class CrimsonDesertSaveAdapter(GameAdapter):
    name = "crimson-desert-save"

    def __init__(
        self,
        catalogue: Catalogue,
        *,
        save_path: str | Path | None = None,
        editor_dir: str | Path | None = None,
        backup_dir: str | Path = "backups",
        steam_manifest: str | Path | None = None,
        campaigns_file: str | Path | None = None,
    ):
        self.catalogue = catalogue
        self.backup_dir = Path(backup_dir)
        self.steam_manifest = steam_manifest
        self._save_override = Path(save_path) if save_path else None
        self.backend: NattKhBackend | None = None
        self.backend_error: str | None = None
        if editor_dir:
            try:
                self.backend = NattKhBackend(editor_dir)
            except BackendUnavailable as exc:
                self.backend_error = str(exc)
        self._cache: tuple[str, DeepState | None] | None = None
        self._version: GameVersion | None = None
        self.registry = CampaignRegistry(campaigns_file or Path(backup_dir).parent / "campaigns.json")
        self.identity_problem: str | None = None
        self._failed: dict[tuple, Result] = {}
        self.transforms: dict[str, BlobTransform] = {}  # game_ref kind -> verified transform
        self.transforms["nothing"] = lambda blob, entries: blob
        if self.backend is not None:
            self.transforms["knowledge"] = self._learn_knowledge
            self.transforms["inventory_item"] = self._give_items

    # ------------------------------------------------------------------ helpers
    @property
    def save_path(self) -> Path | None:
        if self._save_override:
            return self._save_override
        saves = find_saves()
        return saves[0].path if saves else None

    def _read(self) -> bytes | None:
        path = self.save_path
        if path is None or not path.is_file():
            return None
        return path.read_bytes()

    def _deep_state(self) -> DeepState | None:
        data = self._read()
        if data is None or self.backend is None:
            return None
        digest = hashlib.sha256(data).hexdigest()
        if self._cache and self._cache[0] == digest:
            return self._cache[1]
        container = save_container.decode(data)  # HMAC failure raises: never trust a corrupt save
        state = self.backend.decode(container.blob)
        self._cache = (digest, state)
        return state

    # ------------------------------------------------------------------ reading
    def get_game_version(self) -> GameVersion:
        if self._version is None:
            self._version = detect_version(self.catalogue.compatibility, self.steam_manifest)
        return self._version

    def get_save_identity(self) -> SaveIdentity | None:
        path = self.save_path
        if path is None or not path.is_file():
            self.identity_problem = None
            return None
        if self.backend is None:
            # Without the decoder there is no history to go by. Binding to the slot instead would tie the seed
            # to an autosave slot that the game rotates, so refuse until the decoder is set up.
            self.identity_problem = ("the save editor folder is not set up, so your saves cannot be read "
                                     f"({self.backend_error or 'no --editor-dir given'}); restart the client with "
                                     "--editor-dir pointing at the CrimsonSaveEditor folder")
            return None
        try:
            state = self._deep_state()
        except save_container.SaveFormatError as exc:
            self.identity_problem = f"the newest save cannot be read ({exc})"
            return None
        if state is None:
            return None
        found = self.registry.identify(state, slot_from_path(path).account, now=datetime.now().isoformat(timespec="seconds"))
        self.identity_problem = found.problem
        return SaveIdentity(found.campaign.id, str(path)) if found.campaign else None

    def _detected(self, detection: dict[str, Any], state: DeepState) -> bool:
        kind = detection.get("kind")
        if kind == "quest_state":
            entry = state.quests.get(int(detection["quest_key"]))
            return entry is not None and entry.get("state") in set(detection["states"])
        if kind == "mission_state":
            entry = state.missions.get(int(detection["mission_key"]))
            if entry is not None and entry.get("state") in set(detection["states"]):
                return True
            # an objective counts as done once its mission is, even if the player skipped it
            parent = detection.get("parent_mission_key")
            return parent is not None and (state.missions.get(int(parent)) or {}).get("state") == 5
        if kind == "knowledge":
            return int(detection["knowledge_key"]) in state.knowledge
        return False  # placeholder and unknown kinds are never reported

    def get_completed_checks(self, catalogue: Catalogue) -> set[int]:
        state = self._deep_state()
        if state is None:
            return set()
        done = set()
        for loc in catalogue.locations.values():
            if (loc.detectable or (self.include_drafts and loc.draft_detectable)) \
                    and self._detected(dict(loc.detection), state):
                done.add(loc.ap_id)
        return done

    def is_goal_complete(self, catalogue: Catalogue, goal_key: str) -> bool:
        goal = catalogue.goals.get(goal_key)
        state = self._deep_state()
        if goal is None or state is None or not (goal.verified or self.include_drafts):
            return False
        return self._detected(dict(goal.detection), state)

    def get_inventory_snapshot(self) -> dict[str, int]:
        state = self._deep_state()
        return {str(k): v for k, v in state.items.items()} if state else {}

    def get_progression_state(self) -> dict[str, Any]:
        state = self._deep_state()
        if state is None:
            return {"backend": None, "backend_error": self.backend_error}
        return {"quests": len(state.quests), "missions": len(state.missions), "knowledge": len(state.knowledge)}

    def describe_location_support(self, loc: LocationDef) -> str:
        return "detectable" if loc.detectable else "not detectable (placeholder or unverified)"

    # ------------------------------------------------------------------ writing
    def can_grant_item(self, item: ItemDef) -> Result:
        if not (item.deliverable or (self.include_drafts and item.draft_deliverable)):
            return Result.unsupported(f"{item.name!r} has no verified delivery procedure (capability: {item.capability})")
        kind = (item.game_ref or {}).get("kind")
        if kind not in self.transforms:
            return Result.unsupported(f"no verified save transformation for game_ref kind {kind!r} yet")
        return Result.success()

    def safe_to_write(self) -> Result:
        version = self.get_game_version()
        if not version.writes_allowed:
            return Result.unsupported(f"save writes are disabled for game version {version.label}: {version.note}")
        running = game_running()
        if running is None:
            return Result.failure("cannot tell whether Crimson Desert is running; refusing to write")
        if running:
            return Result.failure("Crimson Desert is running; rewards are applied once you quit to desktop")
        data = self._read()
        if data is None:
            return Result.failure("no save file found")
        try:
            container = save_container.decode(data)
        except save_container.SaveFormatError as exc:
            return Result.failure(f"save is not readable: {exc}")
        allowed = self.catalogue.compatibility.get("save_container_versions", {}).get(str(container.version), {})
        if not allowed.get("write"):
            return Result.unsupported(f"writing save container version {container.version} is not enabled")
        return Result.success()

    def fingerprint(self) -> str:
        data = self._read()
        return hashlib.sha256(data).hexdigest() if data is not None else "missing"

    def prepare(self, batch: DeliveryBatch) -> PreparedWrite | Result:
        for entry in batch.entries:
            verdict = self.can_grant_item(entry.item)
            if not verdict.ok:
                return verdict
        safe = self.safe_to_write()
        if not safe.ok:
            return safe
        return self._build_and_validate(batch)

    def _build_and_validate(self, batch: DeliveryBatch) -> PreparedWrite | Result:
        path = self.save_path
        assert path is not None
        original = path.read_bytes()
        container = save_container.decode(original)
        before = parc.parse_structure(container.blob)

        pre_fp = hashlib.sha256(original).hexdigest()
        attempt = (pre_fp, tuple(sorted(e.index for e in batch.entries)))
        if attempt in self._failed:
            return self._failed[attempt]   # same save, same items: don't spend another decode finding out

        blob = container.blob
        by_kind: dict[str, list[DeliveryEntry]] = {}
        for entry in batch.entries:
            by_kind.setdefault(entry.item.game_ref["kind"], []).append(entry)
        skipped: dict[str, str] = {}
        for kind, entries in by_kind.items():
            try:
                blob = self.transforms[kind](blob, entries)
            except save_edit.SaveEditError:
                # find the item(s) that can't go in right now; deliver the rest
                for entry in entries:
                    try:
                        blob = self.transforms[kind](blob, [entry])
                    except save_edit.SaveEditError as exc:
                        skipped[str(entry.index)] = f"{entry.item.name}: {exc}"
        if len(skipped) == len(batch.entries):
            failure = Result.failure("; ".join(dict.fromkeys(skipped.values())), skipped=skipped)
            self._failed = {attempt: failure}
            return failure
        if blob == container.blob:
            # everything in the batch is already in the save (e.g. Flight learned in the meantime)
            return PreparedWrite(batch_id=batch.batch_id, pre_fingerprint=pre_fp, post_fingerprint=pre_fp, temp_path=None,
                                 plan={"skipped": skipped})

        encoded = save_container.encode(blob, container.header)
        # validate by decoding what we are about to write, exactly as the game would
        check = save_container.decode(encoded)
        after = parc.parse_structure(check.blob)
        if check.blob != blob:
            return Result.failure("round-trip validation failed: re-decoded save differs from the intended content")
        if after.schema_sha256 != before.schema_sha256:
            return Result.failure("validation failed: the transformation changed the save schema")

        backup_save(path, self.backup_dir, label=f"before-{batch.batch_id[:8]}")
        temp = path.with_name(path.name + f".crimsonipelago-{batch.batch_id[:8]}")
        with open(temp, "wb") as fh:
            fh.write(encoded)
            fh.flush()
            os.fsync(fh.fileno())
        return PreparedWrite(
            batch_id=batch.batch_id,
            pre_fingerprint=pre_fp,
            post_fingerprint=hashlib.sha256(encoded).hexdigest(),
            temp_path=str(temp),
            plan={"skipped": skipped},
        )

    def commit(self, prepared: PreparedWrite) -> Result:
        if prepared.temp_path is None and prepared.pre_fingerprint == prepared.post_fingerprint:
            return self.verify_reward(prepared)   # nothing to write
        path = self.save_path
        if path is None or prepared.temp_path is None or not Path(prepared.temp_path).is_file():
            return Result.failure("prepared save is missing")
        if self.fingerprint() != prepared.pre_fingerprint:
            return Result.failure("the save changed after the delivery was prepared; not overwriting it")
        running = game_running()
        if running is not False:
            return Result.failure("Crimson Desert started (or cannot be checked) before the write; not overwriting")
        os.replace(prepared.temp_path, path)
        return self.verify_reward(prepared)

    def save_written_after(self, timestamp: float) -> bool:
        path = self.save_path
        try:
            return path is not None and path.stat().st_mtime > timestamp
        except OSError:
            return False

    def is_game_running(self) -> bool | None:
        return game_running()

    def needs_write(self, item: ItemDef) -> bool:
        return (item.game_ref or {}).get("kind") != "nothing"

    def missing_unlocks(self, items) -> list[ItemDef]:
        state = self._deep_state()
        if state is None:
            return []
        return [i for i in items if (i.game_ref or {}).get("kind") == "knowledge"
                and int(i.game_ref["knowledge_key"]) not in state.knowledge]

    def setup_warnings(self) -> list[str]:
        """Problems with the player's setup that make the randomizer misbehave without breaking anything."""
        if self.backend is None:
            return [f"Save editor folder not usable ({self.backend_error or 'no --editor-dir given'}): checks can't be "
                    "read and items can't be delivered. Restart with --editor-dir pointing at your CrimsonSaveEditor "
                    "folder (the one with save_parser.py)."]

        return []                       # the game-data override is checked per seed, once connected


    def override_advice(self, story_items, slice_no: int = 99):
        """The seed's game-data override against the installed one (overrides.advise)."""
        return advise(self.catalogue, story_items, slice_no)

    def override_warnings(self, story_items, slice_no: int = 99) -> list[str]:
        message = self.override_advice(story_items, slice_no).message()
        return [message] if message else []

    # ------------------------------------------------------------------ transformations
    def _give_items(self, blob: bytes, entries: list[DeliveryEntry]) -> bytes:
        gifts = [save_edit.Gift(int(e.item.game_ref["item_key"]), int(e.item.game_ref.get("count", 1)),
                                e.item.game_ref.get("max_stack")) for e in entries]
        new, _report = save_edit.give_items(blob, self.backend, gifts)
        return new

    def _learn_knowledge(self, blob: bytes, entries: list[DeliveryEntry]) -> bytes:
        keys = [int(e.item.game_ref["knowledge_key"]) for e in entries]
        new, _report = save_edit.learn_knowledge(blob, self.backend, keys)
        return new

    def discard(self, prepared: PreparedWrite) -> None:
        if prepared.temp_path:
            try:
                os.remove(prepared.temp_path)
            except FileNotFoundError:
                pass
