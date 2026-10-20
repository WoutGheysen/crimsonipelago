"""Detect the installed Crimson Desert build and gate unsafe operations (scope doc 6.6).

The Steam build id comes from steamapps/appmanifest_3321460.acf. It changes
with every game update, which is exactly what save-format compatibility
depends on, so it is the key compatibility.json is written against.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .interface import GameVersion
from .save_locator import STEAM_APP_ID

_ACF_FIELD = re.compile(r'"(\w+)"\s+"([^"]*)"')


def candidate_steam_libraries() -> list[Path]:
    libs: list[Path] = []
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env)
        if base:
            libs.append(Path(base) / "Steam" / "steamapps")
    home = Path.home()
    libs += [home / ".steam/steam/steamapps", home / ".local/share/Steam/steamapps"]
    # extra libraries listed in libraryfolders.vdf
    for lib in list(libs):
        vdf = lib / "libraryfolders.vdf"
        if vdf.is_file():
            for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="replace")):
                libs.append(Path(m.group(1).replace("\\\\", "\\")) / "steamapps")
    seen, out = set(), []
    for lib in libs:
        if lib not in seen:
            seen.add(lib)
            out.append(lib)
    return out


def read_appmanifest(path: str | Path) -> dict[str, str]:
    return dict(_ACF_FIELD.findall(Path(path).read_text(encoding="utf-8", errors="replace")))


def find_appmanifest(override: str | Path | None = None) -> Path | None:
    if override:
        p = Path(override)
        if p.is_dir():
            p = p / f"appmanifest_{STEAM_APP_ID}.acf"
        return p if p.is_file() else None
    for lib in candidate_steam_libraries():
        p = lib / f"appmanifest_{STEAM_APP_ID}.acf"
        if p.is_file():
            return p
    return None


def detect_version(compatibility: dict[str, Any], manifest_override: str | Path | None = None) -> GameVersion:
    manifest = find_appmanifest(manifest_override)
    if manifest is None:
        return GameVersion(None, "unknown (Steam manifest not found)", supported=False, writes_allowed=False,
                           note="Point the client at your Steam library to enable version checks.")
    fields = read_appmanifest(manifest)
    build = fields.get("buildid")
    return classify_build(build, compatibility)


def classify_build(build: str | None, compatibility: dict[str, Any]) -> GameVersion:
    for entry in compatibility.get("supported_builds", []):
        if str(entry.get("buildid")) == str(build):
            return GameVersion(
                build, entry.get("label", f"build {build}"), supported=True,
                writes_allowed=bool(entry.get("writes_enabled", False)), note=entry.get("note", ""),
            )
    return GameVersion(build, f"build {build}" if build else "unknown", supported=False, writes_allowed=False,
                       note="This game build has not been tested with Crimsonipelago; save writes are disabled.")
