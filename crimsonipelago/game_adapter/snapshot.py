"""Normalized, JSON-serialisable snapshots of a Crimson Desert save."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import parc, save_container
from .save_locator import slot_from_path

SNAPSHOT_VERSION = 1


def snapshot_bytes(data: bytes, *, path: str | Path | None = None, backend=None, deep_fields: bool = False) -> dict[str, Any]:
    snap: dict[str, Any] = {
        "snapshot_version": SNAPSHOT_VERSION,
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {
            "path": str(path) if path else None,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        },
        "warnings": [],
    }
    if path:
        slot = slot_from_path(path)
        snap["save_identity"] = {"account": slot.account, "slot": slot.slot, "profile_id": slot.profile_id}

    container = save_container.decode(data, require_hmac=False)
    snap["container"] = container.describe()
    if not container.hmac_ok:
        snap["warnings"].append("HMAC mismatch: this save would be rejected; snapshot is for diagnosis only")

    raw = container.blob
    snap["blob_sha256"] = hashlib.sha256(raw).hexdigest()
    try:
        structure = parc.parse_structure(raw)
    except parc.ParcFormatError as exc:
        snap["warnings"].append(f"structure parse failed: {exc}")
        return snap
    snap["structure"] = {
        "root_type": structure.root_type,
        "type_count": len(structure.types),
        "schema_sha256": structure.schema_sha256,
        "objects": parc.object_digests(raw, structure),
    }
    snap["warnings"].extend(structure.warnings)

    if backend is not None:
        try:
            deep = backend.decode(raw, include_fields=deep_fields)
        except Exception as exc:  # a backend failure must never hide the structural snapshot
            snap["warnings"].append(f"{getattr(backend, 'name', 'backend')} decode failed: {exc}")
        else:
            snap["state"] = {
                "backend": getattr(backend, "name", "unknown"),
                "quests": {str(k): v for k, v in sorted(deep.quests.items())},
                "missions": {str(k): v for k, v in sorted(deep.missions.items())},
                "knowledge": sorted(deep.knowledge),
                "items": {str(k): v for k, v in sorted(deep.items.items())},
            }
            if deep_fields:
                snap["fields"] = dict(sorted(deep.fields.items()))
            snap["warnings"].extend(deep.warnings)
    return snap


def snapshot_file(path: str | Path, *, backend=None, deep_fields: bool = False) -> dict[str, Any]:
    p = Path(path)
    return snapshot_bytes(p.read_bytes(), path=p, backend=backend, deep_fields=deep_fields)
