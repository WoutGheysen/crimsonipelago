"""crimson-build-apworld: package apworld/crimson_desert into crimson_desert.apworld.

An .apworld is a zip whose single top-level folder is the world package.
Files matching the patterns in the world's .apignore (gitignore-style subset:
`name/` for folders anywhere, glob patterns for files) are left out.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
import zipfile
from pathlib import Path

WORLD_NAME = "crimson_desert"
DEFAULT_SOURCE = Path(__file__).resolve().parents[2] / "apworld" / WORLD_NAME
# Fixed timestamp so two builds of the same tree are byte-identical.
ZIP_DATE = (2026, 1, 1, 0, 0, 0)
# Archipelago's APWorldContainer format version (worlds/Files.py, Archipelago 0.6.x).
APWORLD_CONTAINER_VERSION = 7


def read_ignore(source: Path) -> list[str]:
    ignore = source / ".apignore"
    patterns = ["__pycache__/", "*.pyc", ".apignore"]
    if ignore.is_file():
        for line in ignore.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                patterns.append(line)
    return patterns


def is_ignored(rel: Path, patterns: list[str]) -> bool:
    posix = rel.as_posix()
    for pat in patterns:
        if pat.endswith("/"):
            folder = pat.rstrip("/")
            if "/" in folder:  # anchored path such as test/__pycache__
                if posix == folder or posix.startswith(folder + "/"):
                    return True
            elif folder in rel.parts[:-1] or posix == folder:
                return True
        elif fnmatch.fnmatch(rel.name, pat) or fnmatch.fnmatch(posix, pat):
            return True
    return False


def collect(source: Path) -> list[Path]:
    patterns = read_ignore(source)
    files = [p for p in source.rglob("*") if p.is_file() and not is_ignored(p.relative_to(source), patterns)]
    return sorted(files, key=lambda p: p.relative_to(source).as_posix())


def build(source: Path, output: Path) -> Path:
    manifest = source / "archipelago.json"
    if not (source / "__init__.py").is_file() or not manifest.is_file():
        raise SystemExit(f"{source} does not look like the {WORLD_NAME} world (missing __init__.py or archipelago.json)")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        for path in collect(source):
            rel = path.relative_to(source).as_posix()
            data = packaged_manifest(path) if rel == "archipelago.json" else path.read_bytes()
            info = zipfile.ZipInfo(f"{WORLD_NAME}/{rel}", ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
    return output


def packaged_manifest(path: Path) -> bytes:
    """The source manifest plus the container fields Archipelago requires inside an .apworld.

    Mirrors Archipelago's own "Build APWorlds" step (APWorldContainer.get_manifest):
    without compatible_version/version, Archipelago 0.6.x warns and 0.7.0 refuses to load the world.
    """
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if "game" not in manifest or "world_version" not in manifest:
        raise SystemExit(f"{path} must define 'game' and 'world_version'")
    manifest.update({"compatible_version": APWORLD_CONTAINER_VERSION, "version": APWORLD_CONTAINER_VERSION})
    return json.dumps(manifest, indent=2).encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="crimson-build-apworld", description=__doc__.splitlines()[0])
    p.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="world package folder")
    p.add_argument("-o", "--output", type=Path, default=Path("dist") / f"{WORLD_NAME}.apworld")
    args = p.parse_args(argv)
    out = build(args.source, args.output)
    with zipfile.ZipFile(out) as z:
        count = len(z.namelist())
    print(f"wrote {out} ({count} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
