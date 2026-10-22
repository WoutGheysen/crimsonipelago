"""crimson-data: explore Crimson Desert's game data (read-only).

    crimson-data groups                         archive groups in load order
    crimson-data types                          how many files of each extension (short overview)
    crimson-data find PATTERN                   files whose path or name matches a glob
    crimson-data extract PATTERN -o DIR         unpack matching files (keeps their folder structure)
    crimson-data tables DIR                     static-info tables found in DIR, with record counts
    crimson-data records DIR TABLE [--name RE]  keys and names of a table's records
    crimson-data search DIR VALUE...            which records contain a 32-bit value (e.g. a key)
    crimson-data dump DIR TABLE KEY             one record: strings, known-key references, hex

The game folder is found through Steam; pass --game to override. The game's files are never modified.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ..game_adapter.version_detection import candidate_steam_libraries
from ..game_data import tables as tb
from ..game_data.archive import ArchiveError, GameArchive

GAME_FOLDER = "Crimson Desert"
DEFAULT_EXTRACT_LIMIT = 200


def find_game_dir(override: str | None = None) -> Path:
    candidates = [Path(override)] if override else []
    if not override and os.environ.get("CRIMSON_DESERT_DIR"):
        candidates.append(Path(os.environ["CRIMSON_DESERT_DIR"]))
    if not candidates:
        candidates = [lib / "common" / GAME_FOLDER for lib in candidate_steam_libraries()]
    for c in candidates:
        if (c / "meta" / "0.papgt").is_file():
            return c
    tried = ", ".join(str(c) for c in candidates) or "(none)"
    raise SystemExit(f"Crimson Desert not found (tried {tried}). Pass --game \"<...>\\steamapps\\common\\Crimson Desert\".")


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def cmd_groups(args) -> int:
    ga = GameArchive(find_game_dir(args.game))
    print(f"{ga.root}  ({len(ga.groups)} groups, first match wins)")
    for g in ga.groups:
        present = (ga.root / g.name / "0.pamt").is_file()
        files = len(ga.pamt(g.name).files) if present else 0
        flag = "optional" if g.is_optional else ""
        print(f"  {g.name}  lang 0x{g.language:04X}  {flag:<8}  "
              f"{files:6d} files" + ("" if present else "  (missing on disk)"))
    return 0


def cmd_types(args) -> int:
    ga = GameArchive(find_game_dir(args.game))
    counts: dict[str, list[int]] = {}
    for vf in ga.files("*"):
        ext = vf.name.rsplit(".", 1)[-1].lower() if "." in vf.name else "(none)"
        c = counts.setdefault(ext, [0, 0])
        c[0] += 1
        c[1] += vf.size
    for ext, (n, size) in sorted(counts.items(), key=lambda kv: -kv[1][0]):
        print(f"{n:8d}  {_size(size):>9}  .{ext}")
    print(f"{sum(c[0] for c in counts.values())} files, {len(counts)} extensions")
    return 0


def cmd_find(args) -> int:
    ga = GameArchive(find_game_dir(args.game))
    hits = ga.files(args.pattern, all_copies=args.all_copies)
    for vf in hits:
        print(f"{vf.group}  {_size(vf.size):>9}  {vf.compression:<5} {vf.crypto:<8} {vf.path}")
    print(f"{len(hits)} file(s)")
    return 0


def cmd_extract(args) -> int:
    ga = GameArchive(find_game_dir(args.game))
    hits = ga.files(args.pattern)
    if not hits:
        print("no files match")
        return 1
    if len(hits) > args.limit:
        print(f"{len(hits)} files match; refusing to extract more than {args.limit} (use --limit to raise it)")
        return 1
    out = Path(args.output)
    failed = 0
    for vf in hits:
        dest = out / vf.path
        try:
            data = ga.read(vf)
        except (ArchiveError, OSError, ValueError) as exc:
            print(f"skipped {vf.path}: {exc}")
            failed += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        print(f"{_size(len(data)):>9}  {dest}")
    print(f"extracted {len(hits) - failed} file(s) to {out}" + (f", {failed} failed" if failed else ""))
    return 1 if failed else 0


def _tables(folder: str) -> dict[str, tb.Table]:
    problems: list[str] = []
    tables = tb.load_tables(folder, problems=problems)
    for p in problems:
        print(f"warning: {p}", file=sys.stderr)
    if not tables:
        raise SystemExit(f"no tables found under {folder} (expected .staticinfobody/.staticinfoheader "
                         "or .pabgb/.pabgh pairs; did the extract step match any files?)")
    return tables


def cmd_tables(args) -> int:
    for name, t in _tables(args.dir).items():
        named = sum(1 for r in t.records.values() if r.name)
        print(f"{name:<32} {len(t):6d} records  {named:6d} named  {_size(len(t.body)):>9}")
    return 0


def _table(tables: dict[str, tb.Table], name: str) -> tb.Table:
    if name in tables:
        return tables[name]
    close = [t for t in tables if name.lower() in t.lower()]
    if len(close) == 1:
        return tables[close[0]]
    raise SystemExit(f"no table {name!r}" + (f"; did you mean: {', '.join(close)}" if close else ""))


def cmd_records(args) -> int:
    table = _table(_tables(args.dir), args.table)
    recs = table.by_name(args.name) if args.name else list(table.records.values())
    for r in sorted(recs, key=lambda r: r.key):
        print(f"{r.key:>11}  {len(r.data):6d} B  {r.name}")
    print(f"{len(recs)} record(s)")
    return 0


def _int(text: str) -> int:
    return int(text, 0)


def cmd_search(args) -> int:
    tables = _tables(args.dir)
    names = tb.KeyNames(tables)
    for value in args.values:
        known = names.describe(value)
        print(f"== {value}" + (f"  ({known})" if known else ""))
        hits = tb.search(tables, value)
        for tname, rec, pos in hits:
            print(f"  {tname:<24} {rec.key:>11}  {rec.name or '?':<48} at {', '.join(map(str, pos))}")
        print(f"  {len(hits)} record(s)")
    return 0


def cmd_dump(args) -> int:
    tables = _tables(args.dir)
    table = _table(tables, args.table)
    rec = table.records.get(args.key)
    if rec is None:
        raise SystemExit(f"{table.name} has no record {args.key}")
    names = tb.KeyNames(tables)
    print(f"{table.name} record {rec.key} {rec.name!r}: {len(rec.data)} bytes at body offset {rec.offset}")
    s = tb.strings(rec)
    if s:
        print("strings:")
        for off, text in s:
            print(f"  @{off:<5} {text}")
    refs = tb.references(rec, names, min_value=args.min_key)
    if refs:
        print(f"values that are keys of known records (>= {args.min_key}; small numbers often match by chance):")
        for off, v, desc in refs:
            print(f"  @{off:<5} {v:<11} {desc}")
    if args.hex:
        print("hex:")
        print("\n".join(tb.hexdump(rec.data)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="crimson-data", description="Explore Crimson Desert game data (read-only)",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--game", help="game folder (…\\steamapps\\common\\Crimson Desert); found via Steam by default")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("groups", help="archive groups in load order").set_defaults(func=cmd_groups)
    sub.add_parser("types", help="file count per extension").set_defaults(func=cmd_types)

    f = sub.add_parser("find", help="list files matching a glob")
    f.add_argument("pattern", help='e.g. "*.pabgh" or "*skill*"')
    f.add_argument("--all-copies", action="store_true", help="also show copies shadowed by an earlier group")
    f.set_defaults(func=cmd_find)

    x = sub.add_parser("extract", help="unpack matching files")
    x.add_argument("pattern")
    x.add_argument("-o", "--output", default="gamedata_extracted")
    x.add_argument("--limit", type=int, default=DEFAULT_EXTRACT_LIMIT, help="refuse to unpack more files than this")
    x.set_defaults(func=cmd_extract)

    t = sub.add_parser("tables", help="list static-info tables in a folder")
    t.add_argument("dir")
    t.set_defaults(func=cmd_tables)

    r = sub.add_parser("records", help="keys and names of a table")
    r.add_argument("dir")
    r.add_argument("table")
    r.add_argument("--name", help="regular expression on record names, e.g. CrowWing")
    r.set_defaults(func=cmd_records)

    s = sub.add_parser("search", help="records that contain a 32-bit value")
    s.add_argument("dir")
    s.add_argument("values", type=_int, nargs="+")
    s.set_defaults(func=cmd_search)

    d = sub.add_parser("dump", help="show one record")
    d.add_argument("dir")
    d.add_argument("table")
    d.add_argument("key", type=_int)
    d.add_argument("--hex", action="store_true")
    d.add_argument("--min-key", type=int, default=1000, help="ignore smaller values when listing references")
    d.set_defaults(func=cmd_dump)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ArchiveError, tb.TableError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
