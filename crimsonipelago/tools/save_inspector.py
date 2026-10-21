"""crimson-inspect: read-only Crimson Desert save inspector (scope doc 3.3).

    crimson-inspect locate                       list save files found on this machine
    crimson-inspect info SAVE                    container + structure summary
    crimson-inspect backup SAVE [--to DIR]       immutable timestamped copy
    crimson-inspect snapshot SAVE [-o FILE]      normalized JSON snapshot
    crimson-inspect diff A B                     what changed (saves or snapshot JSONs)
    crimson-inspect watch [SAVE] [--out DIR]     snapshot on every in-game save (all slots by default)

Nothing here ever writes to a save file.
Add --editor-dir PATH (your CrimsonSaveEditor checkout) for quest / knowledge / item detail.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from ..game_adapter import save_diff, snapshot
from ..game_adapter.nattkh_backend import BackendUnavailable, NattKhBackend, load_name_tables
from ..game_adapter.save_container import SaveFormatError
from ..game_adapter.save_locator import backup_save, candidate_save_roots, find_saves, slot_from_path


def _backend(args):
    if not getattr(args, "editor_dir", None):
        return None
    try:
        return NattKhBackend(args.editor_dir)
    except BackendUnavailable as exc:
        print(f"warning: {exc}; continuing with structure-only snapshots", file=sys.stderr)
        return None


def _load(path: str, args) -> dict:
    p = Path(path)
    if p.suffix.lower() == ".json":
        return json.loads(p.read_text(encoding="utf-8"))
    return snapshot.snapshot_file(p, backend=_backend(args), deep_fields=args.fields)


def _resolve_save(path: str | None) -> Path:
    if path:
        return Path(path)
    saves = find_saves()
    if not saves:
        raise SystemExit("no Crimson Desert save found; pass the path to save.save explicitly")
    return saves[0].path


def cmd_locate(args) -> int:
    saves = find_saves(args.root)
    if not saves:
        print("No saves found. Pass --root with your save folder (…/Pearl Abyss/CD/save).")
        return 1
    for s in saves:
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(s.mtime))}  {s.profile_id:<24} {s.path}")
    return 0


def cmd_info(args) -> int:
    snap = snapshot.snapshot_file(_resolve_save(args.save), backend=_backend(args))
    c = snap["container"]
    print(f"file      {snap['source']['path']} ({snap['source']['size']} bytes)")
    print(f"container version {c['version']}  flags 0x{c['flags']:04X}  hmac {'OK' if c['hmac_ok'] else 'MISMATCH'}"
          f"  payload {c['uncompressed_size']} bytes")
    if "structure" in snap:
        s = snap["structure"]
        print(f"schema    root {s['root_type']}  {s['type_count']} types  sha256 {s['schema_sha256'][:16]}")
        print(f"objects   {len(s['objects'])}")
        for obj in s["objects"]:
            print(f"  {obj['index']:>3}  {obj['class']:<40} {obj['size']:>9} bytes")
    if "state" in snap:
        st = snap["state"]
        print(f"decoded   {len(st['quests'])} quests, {len(st['missions'])} missions, "
              f"{len(st['knowledge'])} knowledge entries, {len(st['items'])} item kinds")
    for w in snap["warnings"]:
        print(f"warning: {w}")
    return 0


def cmd_backup(args) -> int:
    dest = backup_save(_resolve_save(args.save), args.to, label=args.label or "")
    print(dest)
    return 0


def cmd_snapshot(args) -> int:
    snap = snapshot.snapshot_file(_resolve_save(args.save), backend=_backend(args), deep_fields=args.fields)
    text = json.dumps(snap, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(args.output)
    else:
        print(text)
    return 0


def cmd_diff(args) -> int:
    a, b = _load(args.a, args), _load(args.b, args)
    d = save_diff.diff_snapshots(a, b)
    if args.json:
        print(json.dumps(d.to_dict(), indent=2, default=str))
    else:
        print(save_diff.render_diff(d, load_name_tables(args.editor_dir), max_fields=args.max_fields))
    return 0


class Watcher:
    """Snapshot every save file that changes, across all slots.

    Watching a single file misses progress when the game starts writing to another slot, so by
    default every ``*.save`` under the save root is watched and new files are picked up as they appear.
    A failure to read one write is reported and skipped; it never stops the watcher.
    """

    def __init__(self, targets: list[Path], out: Path, *, backend=None, names=None, backup: str | None = None,
                 deep_fields: bool = False, max_fields: int = 200, settle: float = 1.0, printer=print):
        self.targets, self.out = targets, out
        self.backend, self.names, self.backup = backend, names, backup
        self.deep_fields, self.max_fields, self.settle, self.print = deep_fields, max_fields, settle, printer
        self.mtimes: dict[Path, float] = {}
        self.previous: dict[Path, dict] = {}
        self.last_any: tuple[Path, dict] | None = None
        out.mkdir(parents=True, exist_ok=True)

    def files(self) -> list[Path]:
        found: set[Path] = set()
        for t in self.targets:
            if t.is_file():
                found.add(t)
            elif t.is_dir():
                found.update(f for f in t.glob("*/*/*.save") if f.is_file())
                found.update(f for f in t.glob("*/*.save") if f.is_file())
        return sorted(found)

    @staticmethod
    def label(path: Path) -> str:
        slot = slot_from_path(path)
        name = f"{slot.account}-{slot.slot}"
        return name if path.name == "save.save" else f"{name}-{path.stem}"

    def poll(self) -> list[Path]:
        """Check every file once; returns the snapshot files written."""
        written = []
        for f in self.files():
            try:
                mtime = f.stat().st_mtime
            except OSError:
                continue
            if self.mtimes.get(f) == mtime:
                continue
            self.mtimes[f] = mtime
            if self.settle:
                time.sleep(self.settle)  # let the game finish writing
            try:
                snap = snapshot.snapshot_file(f, backend=self.backend, deep_fields=self.deep_fields)
            except Exception as exc:  # never let one unreadable write stop a research session
                self.print(f"skipped a write to {f} that could not be read: {type(exc).__name__}: {exc}")
                continue
            stamp = time.strftime("%Y%m%d-%H%M%S")
            path = self.out / f"{stamp}_{self.label(f)}.json"
            n = 1
            while path.exists():
                n += 1
                path = self.out / f"{stamp}_{self.label(f)}_{n}.json"
            path.write_text(json.dumps(snap, indent=2), encoding="utf-8")
            if self.backup:
                try:
                    backup_save(f, self.backup, label="watch")
                except OSError as exc:
                    self.print(f"warning: could not back up {f}: {exc}")
            self.print(f"\n=== {stamp}  {self.label(f)}  snapshot {path.name}")
            base = self.previous.get(f)
            if base is None and self.last_any is not None:
                other, base = self.last_any
                self.print(f"(first write to this file; compared with the last snapshot of {self.label(other)})")
            if base is not None:
                self.print(save_diff.render_diff(save_diff.diff_snapshots(base, snap), self.names,
                                                 max_fields=self.max_fields))
            self.previous[f] = snap
            self.last_any = (f, snap)
            written.append(path)
        return written


def cmd_watch(args) -> int:
    if args.save:
        targets = [Path(args.save)]
    else:
        targets = [Path(args.root)] if args.root else [r for r in candidate_save_roots() if r.is_dir()]
        if not targets:
            raise SystemExit("no Crimson Desert save folder found; pass --root …/Pearl Abyss/CD/save or a save file")
    watcher = Watcher(targets, Path(args.out), backend=_backend(args), names=load_name_tables(args.editor_dir),
                      backup=args.backup, deep_fields=args.fields, max_fields=args.max_fields)
    what = ", ".join(str(t) for t in targets)
    print(f"watching {what} ({len(watcher.files())} save file(s)); snapshots go to {watcher.out}.")
    print("Save in-game to record a step, Ctrl+C to stop.")
    try:
        while True:
            watcher.poll()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="crimson-inspect", description="Read-only Crimson Desert save inspector")
    p.add_argument("--editor-dir", help="CrimsonSaveEditor checkout, for field-level decoding and names")
    p.add_argument("--fields", action="store_true", help="include every decoded raw field (large; for research)")
    sub = p.add_subparsers(dest="command", required=True)

    loc = sub.add_parser("locate")
    loc.add_argument("--root", help="save root folder or a save file")
    loc.set_defaults(func=cmd_locate)

    info = sub.add_parser("info")
    info.add_argument("save", nargs="?")
    info.set_defaults(func=cmd_info)

    bk = sub.add_parser("backup")
    bk.add_argument("save", nargs="?")
    bk.add_argument("--to", default="backups")
    bk.add_argument("--label")
    bk.set_defaults(func=cmd_backup)

    sn = sub.add_parser("snapshot")
    sn.add_argument("save", nargs="?")
    sn.add_argument("-o", "--output")
    sn.set_defaults(func=cmd_snapshot)

    df = sub.add_parser("diff")
    df.add_argument("a")
    df.add_argument("b")
    df.add_argument("--json", action="store_true")
    df.add_argument("--max-fields", type=int, default=200)
    df.set_defaults(func=cmd_diff)

    w = sub.add_parser("watch")
    w.add_argument("save", nargs="?", help="one save.save to watch (default: every slot in the save folder)")
    w.add_argument("--root", help="save root folder to watch (default: found automatically)")
    w.add_argument("--out", default="snapshots")
    w.add_argument("--backup", help="also keep a read-only copy of every save in this folder")
    w.add_argument("--interval", type=float, default=2.0)
    w.add_argument("--max-fields", type=int, default=200)
    w.set_defaults(func=cmd_watch)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SaveFormatError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
