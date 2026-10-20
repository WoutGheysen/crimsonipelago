"""PyInstaller entry point for CrimsonipelagoClient.exe.

Double-clicking the exe opens the desktop UI; command-line arguments work the
same as the `crimsonipelago` command (connect, status, resolve, sim, ...).
"""

import sys

from crimsonipelago.client.main import main

if __name__ == "__main__":
    sys.exit(main())
