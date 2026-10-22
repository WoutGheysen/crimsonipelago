"""Shared fixtures. Makes the APWorld's catalogue importable without installing the package."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "apworld"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from crimson_desert.catalogue import load as load_catalogue  # noqa: E402

from crimsonipelago.client import events as ev  # noqa: E402
from crimsonipelago.client.database import Ledger  # noqa: E402
from crimsonipelago.game_adapter.sim_adapter import SimulatedGameAdapter  # noqa: E402

PLACEHOLDER_CATALOGUE = ROOT / "tests" / "fixtures" / "placeholder_catalogue"


@pytest.fixture
def catalogue():
    """The placeholder world: synthetic items and checks that exercise every client path."""
    return load_catalogue(PLACEHOLDER_CATALOGUE)


@pytest.fixture
def real_catalogue():
    """The catalogue shipped in the APWorld (real game data)."""
    from crimson_desert.catalogue import default_catalogue
    return default_catalogue()


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "ledger.sqlite3")
    yield led
    led.close()


@pytest.fixture
def sim(tmp_path):
    return SimulatedGameAdapter(tmp_path / "sim.json")


@pytest.fixture
def bus():
    b = ev.EventBus()
    b.log = []
    b.subscribe(lambda e: e.kind != ev.STATUS and b.log.append(e))
    return b


def archipelago_path() -> Path | None:
    value = os.environ.get("ARCHIPELAGO_PATH")
    return Path(value) if value and (Path(value) / "MultiServer.py").is_file() else None


def editor_path() -> Path | None:
    value = os.environ.get("NATTKH_EDITOR_DIR")
    return Path(value) if value and (Path(value) / "save_parser.py").is_file() else None
