"""Is the game running? Save writes are refused while it is (scope doc 6.4)."""

from __future__ import annotations

import subprocess
import sys

DEFAULT_PROCESS_NAMES = ("CrimsonDesert.exe",)


def running_processes() -> set[str] | None:
    """Lower-cased process names, or None if they cannot be listed."""
    try:
        import psutil  # optional dependency
    except ImportError:
        psutil = None
    if psutil is not None:
        names = set()
        for proc in psutil.process_iter(["name"]):
            name = proc.info.get("name")
            if name:
                names.add(name.lower())
        return names
    try:
        if sys.platform == "win32":
            out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=10,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            return {line.split('","')[0].strip('"').lower() for line in out.splitlines() if line}
        out = subprocess.run(["ps", "-A", "-o", "comm="], capture_output=True, text=True, timeout=10).stdout
        return {line.strip().rsplit("/", 1)[-1].lower() for line in out.splitlines() if line.strip()}
    except (OSError, subprocess.SubprocessError):
        return None


def game_running(names: tuple[str, ...] = DEFAULT_PROCESS_NAMES) -> bool | None:
    """True / False, or None when it cannot be determined (treated as unsafe by callers)."""
    procs = running_processes()
    if procs is None:
        return None
    return any(n.lower() in procs for n in names)
