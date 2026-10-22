"""Shared fixtures for tests that need a real Archipelago installation."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import zipfile

import pytest

from tests.conftest import archipelago_path

AP = archipelago_path()

CD_YAML = """name: Wout
game: Crimson Desert
Crimson Desert:
  goal: first_slice
  item_pool_mode: expanded
"""
CF_YAML = """name: Partner
game: ChecksFinder
ChecksFinder: {}
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(port: int, proc: subprocess.Popen, timeout: float = 60.0) -> None:
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise RuntimeError(f"MultiServer exited early:\n{proc.stdout.read() if proc.stdout else ''}")
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise TimeoutError("MultiServer did not start listening")


@pytest.fixture(scope="module")
def multiworld(tmp_path_factory):
    work = tmp_path_factory.mktemp("ap")
    players, out = work / "players", work / "output"
    players.mkdir()
    out.mkdir()
    (players / "cd.yaml").write_text(CD_YAML, encoding="utf-8")
    (players / "cf.yaml").write_text(CF_YAML, encoding="utf-8")
    env = {**os.environ, "PYTHONWARNINGS": "ignore", "SKIP_REQUIREMENTS_UPDATE": "1"}
    gen = subprocess.run(
        [sys.executable, "Generate.py", "--player_files_path", str(players), "--outputpath", str(out),
         "--seed", "4242", "--spoiler", "1"],
        cwd=AP, env=env, capture_output=True, text=True, timeout=600)
    assert gen.returncode == 0, gen.stdout[-3000:] + gen.stderr[-3000:]
    archive = next(out.glob("*.zip"))
    with zipfile.ZipFile(archive) as z:
        z.extractall(out)
    multidata = next(out.glob("*.archipelago"))
    spoiler = next(out.glob("*_Spoiler.txt"), None)

    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "MultiServer.py", str(multidata), "--host", "127.0.0.1", "--port", str(port),
         "--disable_save", "--loglevel", "warning"],
        cwd=AP, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        _wait_port(port, proc)
        yield {"url": f"ws://127.0.0.1:{port}", "spoiler": spoiler.read_text() if spoiler else ""}
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
