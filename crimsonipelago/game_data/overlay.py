"""Game-data overrides ("overlays"): patch static-info records and install them as an extra archive group.

An overlay is a numbered group folder (default range 0090+) holding modified copies of a few tables,
registered at the front of meta/0.papgt so the game loads it first. Nothing in the original groups is
touched. Install and uninstall are exact inverses on 0.papgt, and a timestamped copy of 0.papgt is
kept before every change. Steam's "Verify integrity of game files" also restores the original state.

Patches are same-size only (whole 32-bit values and equal-length text inside one record), so record
offsets never move and the table index stays valid. Each patch names the record it expects and how
many replacements it must make; anything unexpected (e.g. after a game update) stops the build.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path

from .archive import ArchiveError, GameArchive, Pamt, Papgt, VirtualFile, build_group
from .checksum import checksum
from .tables import TABLE_SUFFIXES, Table

MARKER = "crimsonipelago_overlay.json"
FIRST_GROUP = 90
BUNDLED = Path(__file__).with_name("patches")


class OverlayError(RuntimeError):
    pass


@dataclass
class RecordPatch:
    table: str
    record: int
    name: str | None = None
    u32: list[tuple[int, int]] = field(default_factory=list)
    text: list[tuple[str, str]] = field(default_factory=list)
    expect_u32: int | None = None
    expect_text: int | None = None


@dataclass
class PatchSpec:
    name: str
    description: str
    patches: list[RecordPatch]
    game_build: str | None = None

    @classmethod
    def load(cls, name_or_path: str | Path) -> PatchSpec:
        path = Path(name_or_path)
        if not path.is_file():
            path = BUNDLED / f"{name_or_path}.json"
        if not path.is_file():
            known = ", ".join(sorted(p.stem for p in BUNDLED.glob("*.json")))
            raise OverlayError(f"no patch {name_or_path!r} (bundled: {known})")
        raw = json.loads(path.read_text(encoding="utf-8"))
        patches = []
        for p in raw["patches"]:
            rp = RecordPatch(p["table"], int(p["record"]), p.get("name"),
                             [(int(a), int(b)) for a, b in p.get("u32", [])],
                             [(str(a), str(b)) for a, b in p.get("text", [])],
                             p.get("expect", {}).get("u32"), p.get("expect", {}).get("text"))
            for old, new in rp.text:
                if len(old.encode("utf-8")) != len(new.encode("utf-8")):
                    raise OverlayError(f"{path.name}: text replacement {old!r} -> {new!r} changes the length")
            patches.append(rp)
        return cls(raw["name"], raw.get("description", ""), patches, raw.get("game_build"))


def _replace_u32(data: bytearray, old: int, new: int, *, skip_key: bool) -> int:
    needle, repl = struct.pack("<I", old), struct.pack("<I", new)
    count, pos = 0, data.find(needle, 4 if skip_key else 0)
    while pos != -1:
        data[pos:pos + 4] = repl
        count += 1
        pos = data.find(needle, pos + 4)
    return count


def _replace_text(data: bytearray, old: str, new: str) -> int:
    a, b = old.encode("utf-8"), new.encode("utf-8")
    count, pos = 0, data.find(a)
    while pos != -1:
        data[pos:pos + len(a)] = b
        count += 1
        pos = data.find(a, pos + len(b))
    return count


def apply_patches(table: Table, patches: list[RecordPatch]) -> tuple[bytes, list[str]]:
    """Return the patched table body and a log. The body keeps its exact size."""
    body = bytearray(table.body)
    log = []
    for p in patches:
        rec = table.records.get(p.record)
        if rec is None:
            raise OverlayError(f"{table.name}: record {p.record} not found (different game version?)")
        if p.name is not None and rec.name != p.name:
            raise OverlayError(f"{table.name} {p.record}: expected record {p.name!r}, found {rec.name!r} "
                               "(different game version?)")
        data = bytearray(rec.data)
        n_u32 = sum(_replace_u32(data, old, new, skip_key=True) for old, new in p.u32)
        n_text = sum(_replace_text(data, old, new) for old, new in p.text)
        if p.expect_u32 is not None and n_u32 != p.expect_u32:
            raise OverlayError(f"{table.name} {p.record}: expected {p.expect_u32} value replacement(s), made {n_u32}")
        if p.expect_text is not None and n_text != p.expect_text:
            raise OverlayError(f"{table.name} {p.record}: expected {p.expect_text} text replacement(s), made {n_text}")
        if len(data) != len(rec.data):
            raise OverlayError("internal error: a patch changed the record size")
        body[rec.offset:rec.offset + len(data)] = data
        log.append(f"{table.name} {p.record} {rec.name!r}: {n_u32} value(s), {n_text} text(s) replaced")
    return bytes(body), log


def _table_files(game: GameArchive, table: str, exclude_groups: set[str]) -> tuple[VirtualFile, VirtualFile]:
    for body_suffix, index_suffix in TABLE_SUFFIXES.items():
        bodies = [vf for vf in game.files(f"{table}{body_suffix}", all_copies=True)
                  if vf.name == f"{table}{body_suffix}" and vf.group not in exclude_groups]
        if bodies:
            body = bodies[0]
            index_path = body.path[: -len(body_suffix)] + index_suffix
            indexes = [vf for vf in game.files(index_path, all_copies=True)
                       if vf.path == index_path and vf.group not in exclude_groups]
            if not indexes:
                raise OverlayError(f"{table}: found {body.path} but not its index")
            return body, indexes[0]
    raise OverlayError(f"table {table!r} not found in the game data")


@dataclass
class BuiltOverlay:
    spec: PatchSpec
    files: dict[str, bytes]
    compression: str
    log: list[str]
    sources: dict[str, str]          # virtual path -> group it was read from


def build(game: GameArchive, spec: PatchSpec) -> BuiltOverlay:
    ours = {g.name for g in installed(game.root)}
    by_table: dict[str, list[RecordPatch]] = {}
    for p in spec.patches:
        by_table.setdefault(p.table, []).append(p)
    files, log, sources, compression = {}, [], {}, "none"
    for table_name, patches in by_table.items():
        body_vf, index_vf = _table_files(game, table_name, ours)
        table = Table(table_name, game.read(body_vf), game.read(index_vf))
        new_body, tlog = apply_patches(table, patches)
        files[body_vf.path] = new_body
        files[index_vf.path] = game.read(index_vf)  # unchanged; shipped as a pair, as the modding notes advise
        sources[body_vf.path], sources[index_vf.path] = body_vf.group, index_vf.group
        compression = "lz4" if body_vf.compression == "lz4" else compression
        log += tlog
    return BuiltOverlay(spec, files, compression, log, sources)


# --------------------------------------------------------------------------- install / uninstall
@dataclass(frozen=True)
class InstalledOverlay:
    name: str          # group folder, e.g. "0090"
    patch: str
    installed_at: str


def installed(game_dir: str | Path) -> list[InstalledOverlay]:
    root = Path(game_dir)
    out = []
    for marker in sorted(root.glob(f"[0-9][0-9][0-9][0-9]/{MARKER}")):
        try:
            info = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = {}
        out.append(InstalledOverlay(marker.parent.name, info.get("patch", "?"), info.get("installed_at", "?")))
    return out


def _check_closed(force: bool) -> None:
    from ..game_adapter.process_check import game_running

    running = game_running()
    if running is None and not force:
        raise OverlayError("cannot tell whether Crimson Desert is running; close it and retry with --force")
    if running:
        raise OverlayError("Crimson Desert is running; close the game first")


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _backup_papgt(root: Path) -> Path:
    src = root / "meta" / "0.papgt"
    dest = root / "meta" / f"0.papgt.crimsonipelago-{time.strftime('%Y%m%d-%H%M%S')}.bak"
    shutil.copy2(src, dest)
    return dest


def install(game_dir: str | Path, built: BuiltOverlay, *, force: bool = False) -> InstalledOverlay:
    root = Path(game_dir)
    _check_closed(force)
    if installed(root):
        raise OverlayError("a Crimsonipelago overlay is already installed; uninstall it first")
    papgt_path = root / "meta" / "0.papgt"
    papgt = Papgt.parse(papgt_path.read_bytes())
    taken = {e.name for e in papgt.entries}
    number = FIRST_GROUP
    while f"{number:04d}" in taken or (root / f"{number:04d}").exists():
        number += 1
    group = f"{number:04d}"

    pamt, chunks = build_group(built.files, compression=built.compression)
    group_dir = root / group
    group_dir.mkdir()
    try:
        for cid, data in chunks.items():
            (group_dir / f"{cid}.paz").write_bytes(data)
        (group_dir / "0.pamt").write_bytes(pamt)
        info = {"patch": built.spec.name, "description": built.spec.description,
                "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"), "files": sorted(built.files),
                "read_from_groups": built.sources, "log": built.log}
        backup = _backup_papgt(root)
        info["papgt_backup"] = backup.name
        (group_dir / MARKER).write_text(json.dumps(info, indent=2), encoding="utf-8")
        papgt.add_front(group, checksum(pamt[12:]))
        _write_atomic(papgt_path, papgt.to_bytes())
        _verify_installed(root, group, built)
    except Exception:
        _remove_group(root, group, created_now=True)
        raise
    return InstalledOverlay(group, built.spec.name, info["installed_at"])


def _verify_installed(root: Path, group: str, built: BuiltOverlay) -> None:
    game = GameArchive(root)
    if game.groups[0].name != group:
        raise OverlayError("verification failed: the overlay is not first in the load order")
    for path, data in built.files.items():
        hits = game.files(path)
        if not hits or hits[0].group != group or game.read(hits[0]) != data:
            raise OverlayError(f"verification failed: {path} does not resolve to the overlay")


def _remove_group(root: Path, group: str, *, created_now: bool = False) -> None:
    """Unregister a group and delete its folder, but only a folder that is provably ours: it carries
    our marker file, or this very install created it (a failed install cleaning up after itself)."""
    papgt_path = root / "meta" / "0.papgt"
    papgt = Papgt.parse(papgt_path.read_bytes())
    if any(e.name == group for e in papgt.entries):
        papgt.remove(group)
        _write_atomic(papgt_path, papgt.to_bytes())
    group_dir = root / group
    if group_dir.is_dir() and (created_now or (group_dir / MARKER).is_file()):
        shutil.rmtree(group_dir)


def uninstall(game_dir: str | Path, *, force: bool = False) -> list[str]:
    root = Path(game_dir)
    _check_closed(force)
    removed = []
    for ov in installed(root):
        _backup_papgt(root)
        _remove_group(root, ov.name)
        removed.append(ov.name)
    GameArchive(root)  # re-validate the registry
    return removed


def pamt_of(group_dir: Path) -> Pamt:
    return Pamt.parse((group_dir / "0.pamt").read_bytes(), group_dir.name)


__all__ = ["ArchiveError", "BuiltOverlay", "InstalledOverlay", "OverlayError", "PatchSpec", "RecordPatch",
           "apply_patches", "build", "install", "installed", "uninstall"]
