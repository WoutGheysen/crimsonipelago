"""Gather the third-party files the Windows release carries into build/bundled (then PyInstaller packs them).

    python packaging/fetch_bundled.py --editor PATH/CrimsonSaveEditor --editor-license PATH/LICENSE.txt \
        --editor-commit SHA --plugin build/plugin/Release/crimsonipelago.asi \
        --loader-zip version-x64.zip --loader-license license --loader-version v10.0.1

- **The save editor's decoder** (NattKh / RicePaddySoftware, MPL-2.0): the three modules the client imports and the
  four name tables, unmodified, with the license and where the source is. They're source files, so the release
  carries their source form, as the MPL asks.
- **Ultimate ASI Loader** (ThirteenAG, MIT), x64, as `version.dll` (the name the game loads), with its license.
- **crimsonipelago.asi**, the game plugin built from this repository.

Each part is optional, so a build can carry what it has; the client says what's missing. A SOURCES.txt next to
the files records where each came from and its SHA-256.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDITOR_FILES = ("save_parser.py", "save_crypto.py", "models.py",
                "quest_names.json", "mission_names.json", "knowledge_keys_all.json", "item_names.json")
EDITOR_URL = "https://github.com/NattKh/CRIMSON-DESERT-SAVE-EDITOR-AND-GAME-MODS"
LOADER_URL = "https://github.com/ThirteenAG/Ultimate-ASI-Loader"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def gather(out: Path, *, editor: Path | None = None, editor_license: Path | None = None, editor_commit: str = "",
           plugin: Path | None = None, loader_zip: Path | None = None, loader_dll: Path | None = None,
           loader_license: Path | None = None, loader_version: str = "") -> list[str]:
    if out.exists():
        shutil.rmtree(out)
    sources: list[str] = []
    if editor:
        dest = out / "CrimsonSaveEditor"
        dest.mkdir(parents=True)
        for name in EDITOR_FILES:
            shutil.copy2(editor / name, dest / name)
            sources.append(f"CrimsonSaveEditor/{name}  {sha(dest / name)}")
        if editor_license:
            shutil.copy2(editor_license, dest / "LICENSE.txt")
        (dest / "SOURCE.txt").write_text(
            "Unmodified files from the CRIMSON-DESERT-SAVE-EDITOR-AND-GAME-MODS project by NattKh / RicePaddySoftware,\n"
            "licensed under the Mozilla Public License 2.0 (LICENSE.txt). Source:\n"
            f"{EDITOR_URL}/tree/{editor_commit or 'main'}/CrimsonSaveEditor\n", encoding="utf-8")
    if plugin or loader_zip or loader_dll:
        (out / "plugin").mkdir(parents=True, exist_ok=True)
    if plugin:
        shutil.copy2(plugin, out / "plugin" / "crimsonipelago.asi")
        sources.append(f"plugin/crimsonipelago.asi  {sha(out / 'plugin' / 'crimsonipelago.asi')}")
    if loader_zip:
        with zipfile.ZipFile(loader_zip) as z:
            dlls = [n for n in z.namelist() if n.lower().endswith(".dll")]
            if len(dlls) != 1:
                raise SystemExit(f"{loader_zip}: expected one DLL, found {dlls}")
            (out / "plugin" / "version.dll").write_bytes(z.read(dlls[0]))
    elif loader_dll:
        shutil.copy2(loader_dll, out / "plugin" / "version.dll")
    if loader_zip or loader_dll:
        if loader_license:
            shutil.copy2(loader_license, out / "plugin" / "ASI-LOADER-LICENSE.txt")
        sources.append(f"plugin/version.dll  {sha(out / 'plugin' / 'version.dll')}  "
                       f"(Ultimate ASI Loader {loader_version or '?'}, x64, MIT: {LOADER_URL})")
    if sources:
        (out / "SOURCES.txt").write_text("\n".join(sources) + "\n", encoding="utf-8")
    return sources


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", type=Path, default=ROOT / "build" / "bundled")
    p.add_argument("--editor", type=Path, help="the CrimsonSaveEditor folder of a NattKh checkout")
    p.add_argument("--editor-license", type=Path)
    p.add_argument("--editor-commit", default="")
    p.add_argument("--plugin", type=Path, help="the built crimsonipelago.asi")
    p.add_argument("--loader-zip", type=Path, help="Ultimate ASI Loader release zip (x64)")
    p.add_argument("--loader-dll", type=Path, help="or the loader DLL itself")
    p.add_argument("--loader-license", type=Path)
    p.add_argument("--loader-version", default="")
    args = p.parse_args(argv)
    for line in gather(args.out, editor=args.editor, editor_license=args.editor_license,
                       editor_commit=args.editor_commit, plugin=args.plugin, loader_zip=args.loader_zip,
                       loader_dll=args.loader_dll, loader_license=args.loader_license,
                       loader_version=args.loader_version):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
