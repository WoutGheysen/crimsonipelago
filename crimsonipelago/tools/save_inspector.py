"""crimson-inspect: read-only Crimson Desert save inspector (scope doc 3.3).

    crimson-inspect locate                       list save files found on this machine
    crimson-inspect info SAVE                    container + structure summary
    crimson-inspect backup SAVE [--to DIR]       immutable timestamped copy
    crimson-inspect snapshot SAVE [-o FILE]      normalized JSON snapshot
    crimson-inspect diff A B                     what changed (saves or snapshot JSONs)
    crimson-inspect watch [SAVE] [--out DIR]     snapshot on every in-game save, print each diff

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
from ..game_adapter.save_locator import backup_save, find_saves


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


def cmd_watch(args) -> int:
    save = _resolve_save(args.save)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    backend, names = _backend(args), load_name_tables(args.editor_dir)
    print(f"watching {save}; snapshots go to {out}. Save in-game to record a step, Ctrl+C to stop.")
    previous, last_mtime = None, None
    try:
        while True:
            try:
                mtime = save.stat().st_mtime
            except OSError:
                time.sleep(args.interval)
                continue
            if mtime != last_mtime:
                time.sleep(1.0)  # let the game finish writing
                try:
                    snap = snapshot.snapshot_file(save, backend=backend, deep_fields=args.fields)
                except (SaveFormatError, OSError) as exc:
                    print(f"skipped a write that could not be read yet: {exc}")
                    last_mtime = mtime
                    continue
                stamp = time.strftime("%Y%m%d-%H%M%S")
                path = out / f"{stamp}.json"
                path.write_text(json.dumps(snap, indent=2), encoding="utf-8")
                if args.backup:
                    backup_save(save, args.backup, label="watch")
                print(f"\n=== {stamp}  snapshot {path.name}")
                if previous is not None:
                    print(save_diff.render_diff(save_diff.diff_snapshots(previous, snap), names,
                                                max_fields=args.max_fields))
                previous, last_mtime = snap, mtime
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
    w.add_argument("save", nargs="?")
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
