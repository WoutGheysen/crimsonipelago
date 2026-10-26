# PyInstaller spec for CrimsonipelagoClient.exe (one-file build).
# Build from the repository root:  pyinstaller packaging/crimsonipelago.spec
# Built on Windows by .github/workflows/release.yml; also builds on Linux for smoke tests.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(ROOT / "apworld"))  # makes the crimson_desert catalogue importable

datas = collect_data_files("crimson_desert", includes=["data/*.json", "archipelago.json"])
datas += collect_data_files("crimsonipelago.game_data", includes=["patches/*.json"])
datas += collect_data_files("crimsonipelago.game_adapter.runtime", includes=["targets.json"])

# Third-party files the release carries (packaging/fetch_bundled.py): the save editor's decoder (MPL-2.0, unmodified,
# as source), the ASI loader (MIT) and the game plugin. A build without them still works; the client says what's
# missing.
BUNDLED = ROOT / "build" / "bundled"
if BUNDLED.is_dir():
    datas += [(str(path), str(Path("bundled") / path.parent.relative_to(BUNDLED)))
              for path in BUNDLED.rglob("*") if path.is_file()]

# The NattKh decoder is imported at runtime from a folder (bundled or the user's own checkout),
# so the stdlib modules it imports must be bundled even though our code never uses them.
editor_stdlib = ["argparse", "collections", "enum", "hashlib", "hmac", "struct", "json", "lz4.block"]

a = Analysis(
    [str(ROOT / "packaging" / "client_entry.py")],
    pathex=[str(ROOT), str(ROOT / "apworld")],
    datas=datas,
    hiddenimports=["crimson_desert.catalogue", "crimsonipelago.client.ui.main_window", *editor_stdlib],
    excludes=["tkinter", "pytest", "worlds", "BaseClasses", "Options"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CrimsonipelagoClient",
    console=True,  # keeps CLI subcommands and error output usable from a terminal...
    hide_console="hide-early",  # ...but a double-click opens just the window, no console behind it
    upx=False,
)
