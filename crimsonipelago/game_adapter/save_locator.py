"""Find Crimson Desert saves and make immutable backups.

Save layout (as observed by the save-editor community):
    <LocalAppData>/Pearl Abyss/CD/save/<account id>/slot<N>/save.save
Under Steam Proton the LocalAppData folder lives inside the game's prefix.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

STEAM_APP_ID = 3321460
SAVE_FILENAME = "save.save"
_SLOT_RE = re.compile(r"^slot\d+$", re.IGNORECASE)


@dataclass(frozen=True)
class SaveSlot:
    path: Path
    account: str
    slot: str

    @property
    def profile_id(self) -> str:
        """Stable identity for binding a randomizer seed to one save profile."""
        return f"{self.account}/{self.slot}"

    @property
    def mtime(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0


def candidate_save_roots() -> list[Path]:
    roots: list[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "Pearl Abyss" / "CD" / "save")
    home = Path.home()
    proton_suffix = Path("steamapps/compatdata") / str(STEAM_APP_ID) / "pfx/drive_c/users/steamuser/AppData/Local/Pearl Abyss/CD/save"
    for steam in (home / ".steam/steam", home / ".local/share/Steam", home / ".var/app/com.valvesoftware.Steam/data/Steam"):
        roots.append(steam / proton_suffix)
    return roots


def slot_from_path(path: str | Path) -> SaveSlot:
    """Interpret <...>/<account>/<slotN>/save.save; tolerate arbitrary paths."""
    p = Path(path)
    slot = p.parent.name if _SLOT_RE.match(p.parent.name) else p.parent.name or "manual"
    account = p.parent.parent.name if _SLOT_RE.match(p.parent.name) else "manual"
    return SaveSlot(p, account, slot)


def find_saves(override: str | Path | None = None) -> list[SaveSlot]:
    """All save files, newest first. An override may be a file or a save root folder."""
    if override:
        p = Path(override)
        if p.is_file():
            return [slot_from_path(p)]
        roots = [p]
    else:
        roots = candidate_save_roots()
    found = [slot_from_path(f) for root in roots if root.is_dir() for f in root.glob(f"*/*/{SAVE_FILENAME}")]
    found += [slot_from_path(f) for root in roots if root.is_dir() for f in root.glob(f"*/{SAVE_FILENAME}")
              if _SLOT_RE.match(f.parent.name)]
    unique = {s.path.resolve(): s for s in found}
    return sorted(unique.values(), key=lambda s: s.mtime, reverse=True)


def backup_save(path: str | Path, backup_root: str | Path, label: str = "") -> Path:
    """Copy a save into backup_root as a read-only file. Never overwrites."""
    src = Path(path)
    data = src.read_bytes()
    digest = hashlib.sha256(data).hexdigest()[:12]
    slot = slot_from_path(src)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "-", label).strip("-")
    name = f"{stamp}_{digest}" + (f"_{safe_label}" if safe_label else "") + ".save"
    dest_dir = Path(backup_root) / slot.account / slot.slot
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    if dest.exists():
        raise FileExistsError(dest)
    tmp = dest.with_suffix(".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, dest)
    os.chmod(dest, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
    if hashlib.sha256(dest.read_bytes()).hexdigest()[:12] != digest:
        raise OSError(f"backup verification failed for {dest}")
    return dest


def restore_backup(backup: str | Path, target: str | Path) -> None:
    """Restore a backup over a save, atomically. The backup itself is left untouched."""
    target = Path(target)
    tmp = target.with_name(target.name + ".crimsonipelago-restore")
    shutil.copyfile(backup, tmp)
    os.chmod(tmp, stat.S_IREAD | stat.S_IWRITE)
    with open(tmp, "rb+") as fh:
        os.fsync(fh.fileno())
    os.replace(tmp, target)
