"""What the window's buttons do, without Qt: testable, and shared with the command line.

- `bundle_logs`: one zip with everything a bug report needs (no password, no saves).
- `plugin_state` / `install_plugin`: the game plugin and its ASI loader in the game's bin64 folder.
- `rebind_plan` / `apply_rebind`: move the seed to the playthrough of the newest save.
- `bundled_root` / `default_editor_dir`: files the Windows release carries (the save editor's decoder, the plugin).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

PLUGIN_FILE = "crimsonipelago.asi"
LOADER_FILE = "version.dll"           # Ultimate ASI Loader, renamed: the game doesn't load dinput8.dll
PLUGIN_LOG = "crimsonipelago_plugin.log"
LOADER_MARKERS = (b"Ultimate ASI Loader", "Ultimate ASI Loader".encode("utf-16-le"), b"ThirteenAG")


# --------------------------------------------------------------------------- bundled files
def bundled_root() -> Path | None:
    """Where the release's bundled files are: inside the .exe, or a folder named by CRIMSONIPELAGO_BUNDLED (or
    build/bundled in a source checkout, where packaging/fetch_bundled.py puts them)."""
    candidates = []
    if os.environ.get("CRIMSONIPELAGO_BUNDLED"):
        candidates.append(Path(os.environ["CRIMSONIPELAGO_BUNDLED"]))
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "bundled")
    candidates.append(Path(__file__).resolve().parents[2] / "build" / "bundled")
    return next((c for c in candidates if c.is_dir()), None)


def default_editor_dir() -> Path | None:
    """The save editor's decoder the release carries (MPL-2.0, unmodified), when the user hasn't chosen one."""
    root = bundled_root()
    editor = root / "CrimsonSaveEditor" if root else None
    return editor if editor and (editor / "save_parser.py").is_file() else None


# --------------------------------------------------------------------------- logs
def bundle_logs(data_dir: Path, *, game_dir: Path | None = None, summary: str = "",
                now: dt.datetime | None = None) -> Path:
    """Zip the play logs and the client's setup into <data_dir>/support/. Never the room password or saves."""
    now = now or dt.datetime.now()
    out = Path(data_dir) / "support" / f"crimsonipelago-logs-{now:%Y%m%d-%H%M%S}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for path in sorted((Path(data_dir) / "logs").glob("live-*.jsonl")):
            z.write(path, f"logs/{path.name}")
        for name in ("found-targets.json", "campaigns.json"):
            if (Path(data_dir) / name).is_file():
                z.write(Path(data_dir) / name, name)
        settings = Path(data_dir) / "settings.json"
        if settings.is_file():
            try:
                safe = {k: v for k, v in json.loads(settings.read_text(encoding="utf-8")).items() if k != "password"}
                z.writestr("settings.json", json.dumps(safe, indent=2))
            except ValueError:
                pass
        if game_dir is not None and (Path(game_dir) / "bin64" / PLUGIN_LOG).is_file():
            z.write(Path(game_dir) / "bin64" / PLUGIN_LOG, PLUGIN_LOG)
        from .. import __version__
        z.writestr("about.txt", f"Crimsonipelago {__version__}\nmade {now:%Y-%m-%d %H:%M:%S}\n\n{summary}\n")
    return out


# --------------------------------------------------------------------------- plugin
def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass
class PluginState:
    game_dir: Path | None
    plugin: str = "missing"          # missing / installed / outdated
    loader: str = "missing"          # missing / installed / other (a version.dll that isn't the ASI loader)
    bundled: bool = False            # this build carries the plugin files
    notes: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.plugin == "installed" and self.loader in ("installed", "other")

    @property
    def can_install(self) -> bool:
        return self.bundled and self.game_dir is not None and not self.ready


def plugin_state(game_dir: Path | None) -> PluginState:
    root = bundled_root()
    source = root / "plugin" if root else None
    state = PluginState(game_dir, bundled=bool(source and (source / PLUGIN_FILE).is_file()
                                               and (source / LOADER_FILE).is_file()))
    if game_dir is None:
        state.notes.append("the game folder wasn't found")
        return state
    bin64 = Path(game_dir) / "bin64"
    asi, loader = bin64 / PLUGIN_FILE, bin64 / LOADER_FILE
    if asi.is_file():
        state.plugin = "installed"
        if state.bundled and _sha(asi) != _sha(source / PLUGIN_FILE):
            state.plugin = "outdated"
    if loader.is_file():
        data = loader.read_bytes()
        state.loader = "installed" if any(m in data for m in LOADER_MARKERS) else "other"
        if state.loader == "other":
            state.notes.append(f"{LOADER_FILE} in bin64 isn't the Ultimate ASI Loader; left as it is (if it's "
                               "another mod's ASI loader, the plugin loads anyway)")
    return state


def install_plugin(state: PluginState, *, game_running) -> list[str]:
    """Copy the bundled plugin (and the ASI loader, if bin64 has none) into the game's bin64. Never overwrites a
    version.dll that isn't the ASI loader. The game must be closed: its files are in use while it runs."""
    if not state.bundled:
        raise RuntimeError("this build doesn't carry the plugin files (a release build does)")
    if state.game_dir is None:
        raise RuntimeError("the Crimson Desert game folder wasn't found")
    if game_running():
        raise RuntimeError("close Crimson Desert first: the game uses these files while it runs")
    source, bin64 = bundled_root() / "plugin", Path(state.game_dir) / "bin64"
    done = []
    if state.plugin != "installed":
        shutil.copy2(source / PLUGIN_FILE, bin64 / PLUGIN_FILE)
        done.append(f"{'updated' if state.plugin == 'outdated' else 'installed'} {PLUGIN_FILE}")
    if state.loader == "missing":
        shutil.copy2(source / LOADER_FILE, bin64 / LOADER_FILE)
        done.append(f"installed the ASI loader as {LOADER_FILE}")
    return done


# --------------------------------------------------------------------------- rebind
@dataclass
class RebindPlan:
    seed: str | None = None
    session_id: int | None = None
    from_profile: str | None = None
    to_profile: str | None = None
    to_give: list[int] = field(default_factory=list)   # ledger indexes of items given before (may be given again)
    problem: str | None = None                          # why it can't be done
    nothing_to_do: bool = False


def rebind_plan(ledger, adapter, catalogue) -> RebindPlan:
    from .database import DeliveryState

    session = ledger.latest_session()
    if session is None:
        return RebindPlan(problem="No seed yet: connect to one first.")
    plan = RebindPlan(seed=session.seed_name, session_id=session.id, from_profile=session.profile_id)
    profile = adapter.get_current_profile()
    if profile is None:
        problem = getattr(adapter, "identity_problem", None)
        plan.problem = f"Can't tell which playthrough your newest save is from: {problem or 'no save found'}"
        return plan
    plan.to_profile = profile
    if profile == session.profile_id:
        plan.nothing_to_do = True
        return plan
    other = [s for s in ledger.sessions_for_profile(profile) if s.id != session.id]
    if other:
        plan.problem = (f"That playthrough ({profile}) belongs to seed {other[0].seed_name}. Load a save of another "
                        "playthrough first.")
        return plan
    had = ledger.items(session.id, (DeliveryState.COMPLETE, DeliveryState.IN_GAME))
    plan.to_give = [r.idx for r in had if (item := catalogue.item_by_id(r.item_id)) is not None
                    and adapter.needs_write(item)]
    return plan


def apply_rebind(ledger, plan: RebindPlan, *, give_again: bool) -> int:
    if plan.problem or plan.nothing_to_do or plan.session_id is None or plan.to_profile is None:
        raise ValueError(plan.problem or "nothing to do")
    redeliver = plan.to_give if give_again else []
    ledger.rebind_profile(plan.session_id, plan.to_profile, redeliver)
    return len(redeliver)
