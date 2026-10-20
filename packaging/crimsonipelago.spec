# PyInstaller spec for CrimsonipelagoClient.exe (one-file build).
# Build from the repository root:  pyinstaller packaging/crimsonipelago.spec
# Built on Windows by .github/workflows/release.yml; also builds on Linux for smoke tests.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(ROOT / "apworld"))  # makes the crimson_desert catalogue importable

datas = collect_data_files("crimson_desert", includes=["data/*.json", "archipelago.json"])

# The optional NattKh decoder is loaded at runtime from the user's own checkout,
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
    console=True,  # keeps CLI subcommands and error output usable; the UI opens on top
    upx=False,
)
