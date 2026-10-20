"""Crimsonipelago client entry point.

    crimsonipelago                    open the desktop UI (if PySide6 is installed)
    crimsonipelago connect ...        headless client (console activity feed)
    crimsonipelago sim ...            drive the simulated game for testing
    crimsonipelago status             show the local ledger for the last session
    crimsonipelago resolve ID ACTION  settle an interrupted delivery (delivered | retry)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path

from crimson_desert.catalogue import Catalogue, default_catalogue

from .. import __version__
from ..game_adapter.crimson_adapter import CrimsonDesertSaveAdapter
from ..game_adapter.interface import GameAdapter
from ..game_adapter.sim_adapter import SimulatedGameAdapter
from . import events as ev
from .database import BatchState, Ledger
from .item_delivery import DeliveryEngine
from .synchronization import ClientConfig, SyncController

log = logging.getLogger("crimsonipelago")


def default_data_dir() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller: keep data next to the exe
        return Path(sys.executable).resolve().parent / "crimsonipelago_data"
    return Path.cwd() / "crimsonipelago_data"


def settings_path(data_dir: Path) -> Path:
    return data_dir / "settings.json"


def load_settings(data_dir: Path) -> dict:
    try:
        return json.loads(settings_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(data_dir: Path, settings: dict) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    safe = {k: v for k, v in settings.items() if k != "password"}  # never store the room password
    settings_path(data_dir).write_text(json.dumps(safe, indent=2), encoding="utf-8")


def build_adapter(kind: str, catalogue: Catalogue, data_dir: Path, *, sim_file: str | None = None,
                  save_path: str | None = None, editor_dir: str | None = None,
                  steam_manifest: str | None = None) -> GameAdapter:
    if kind == "sim":
        return SimulatedGameAdapter(sim_file or data_dir / "simulated_game.json")
    if kind == "save":
        return CrimsonDesertSaveAdapter(catalogue, save_path=save_path, editor_dir=editor_dir,
                                        backup_dir=data_dir / "backups", steam_manifest=steam_manifest)
    raise ValueError(f"unknown adapter {kind!r}")


def _add_adapter_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--adapter", choices=["save", "sim"], default="save",
                   help="save: real Crimson Desert save files; sim: the simulated game (testing)")
    p.add_argument("--sim-file", help="simulated game file (default: <data-dir>/simulated_game.json)")
    p.add_argument("--save", dest="save_path", help="explicit path to save.save (default: newest save found)")
    p.add_argument("--editor-dir", help="path to your CrimsonSaveEditor checkout (enables check detection)")
    p.add_argument("--steam-manifest", help="Steam library folder or appmanifest_3321460.acf for version checks")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="crimsonipelago", description="Crimson Desert Archipelago client")
    parser.add_argument("--version", action="version", version=f"Crimsonipelago {__version__}")
    parser.add_argument("--data-dir", type=Path, default=None, help="where the ledger, backups and cache live")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command")

    c = sub.add_parser("connect", help="run the client without a window")
    c.add_argument("--server", default=None, help="host[:port], or a ws:// / wss:// URL")
    c.add_argument("--port", type=int, default=None)
    c.add_argument("--slot", default=None, help="your slot (player) name")
    c.add_argument("--password", default=None, help="room password (or set CRIMSONIPELAGO_PASSWORD)")
    _add_adapter_args(c)

    u = sub.add_parser("ui", help="open the desktop UI")
    _add_adapter_args(u)

    s = sub.add_parser("sim", help="drive the simulated game")
    s.add_argument("--sim-file", help="simulated game file (default: <data-dir>/simulated_game.json)")
    ssub = s.add_subparsers(dest="sim_command", required=True)
    init = ssub.add_parser("init", help="create a fresh simulated save")
    init.add_argument("--profile", default="sim/slot1")
    comp = ssub.add_parser("complete", help="complete one or more checks by name")
    comp.add_argument("locations", nargs="+")
    ssub.add_parser("goal", help="complete the goal")
    run = ssub.add_parser("running", help="pretend the game is running or closed")
    run.add_argument("state", choices=["on", "off"])
    ssub.add_parser("show", help="print the simulated save")
    ssub.add_parser("list", help="list check names")

    sub.add_parser("status", help="show the local ledger for the most recent session")
    r = sub.add_parser("resolve", help="settle an interrupted delivery")
    r.add_argument("batch_id", help="delivery id (or its first 8 characters)")
    r.add_argument("decision", choices=["delivered", "retry"])
    _add_adapter_args(r)
    return parser


async def run_headless(controller: SyncController) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, controller.stop)
        except (NotImplementedError, RuntimeError):  # Windows
            pass
    await controller.run()


def cmd_connect(args, data_dir: Path, catalogue: Catalogue) -> int:
    settings = load_settings(data_dir)
    server = args.server or settings.get("server")
    slot = args.slot or settings.get("slot_name")
    if not server or not slot:
        print("error: --server and --slot are required the first time", file=sys.stderr)
        return 2
    password = args.password if args.password is not None else os.environ.get("CRIMSONIPELAGO_PASSWORD", "")
    save_settings(data_dir, {**settings, "server": server, "slot_name": slot})
    adapter = build_adapter(args.adapter, catalogue, data_dir, sim_file=args.sim_file, save_path=args.save_path,
                            editor_dir=args.editor_dir, steam_manifest=args.steam_manifest)
    ledger = Ledger(data_dir / "ledger.sqlite3")
    bus = ev.EventBus()
    bus.subscribe(lambda e: e.kind != ev.STATUS and print(f"{e.at:%H:%M:%S}  {e.message}", flush=True))
    config = ClientConfig(server=server, port=args.port, slot_name=slot, password=password, data_dir=data_dir)
    controller = SyncController(config, adapter, ledger, catalogue, bus)
    try:
        asyncio.run(run_headless(controller))
    except KeyboardInterrupt:
        pass
    finally:
        ledger.close()
    if controller.connection and controller.connection.fatal_error:
        print(f"stopped: {controller.connection.fatal_error}", file=sys.stderr)
        return 1
    return 0


def cmd_sim(args, data_dir: Path, catalogue: Catalogue) -> int:
    path = Path(args.sim_file) if args.sim_file else data_dir / "simulated_game.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if args.sim_command == "init":
        if path.exists():
            path.unlink()
        SimulatedGameAdapter(path, profile_id=args.profile)
        print(f"created {path}")
        return 0
    if args.sim_command == "list":
        for loc in catalogue.locations.values():
            print(f"{loc.ap_id:>6}  {loc.region:<16} {loc.name}")
        return 0
    sim = SimulatedGameAdapter(path)
    if args.sim_command == "complete":
        for name in args.locations:
            if name not in catalogue.locations:
                print(f"unknown check {name!r} (see: crimsonipelago sim list)", file=sys.stderr)
                return 2
            sim.complete_location(name)
            print(f"completed {name}")
    elif args.sim_command == "goal":
        sim.update(goal_complete=True)
        print("goal complete")
    elif args.sim_command == "running":
        sim.update(game_running=args.state == "on")
        print(f"game_running = {args.state == 'on'}")
    elif args.sim_command == "show":
        print(json.dumps(sim.read_state(), indent=2))
    return 0


def cmd_status(data_dir: Path) -> int:
    ledger = Ledger(data_dir / "ledger.sqlite3")
    try:
        session = ledger.latest_session()
        if session is None:
            print("no sessions yet")
            return 0
        print(f"seed {session.seed_name}  slot {session.slot_name} (#{session.slot})  profile {session.profile_id}")
        print(f"items by state: {ledger.state_counts(session.id)}")
        print(f"checks: {ledger.check_counts(session.id)}")
        print(f"goal detected={session.goal_detected} reported={session.goal_reported}")
        for batch in ledger.batches(session.id, (BatchState.RECOVERY, BatchState.PREPARED)):
            print(f"delivery {batch.batch_id} is {batch.state.value}; resolve with: "
                  f"crimsonipelago resolve {batch.batch_id[:8]} delivered|retry")
        print("recent activity:")
        for row in reversed(ledger.recent_events(15)):
            print(f"  {row['at']}  {row['message']}")
    finally:
        ledger.close()
    return 0


def cmd_resolve(args, data_dir: Path, catalogue: Catalogue) -> int:
    ledger = Ledger(data_dir / "ledger.sqlite3")
    try:
        session = ledger.latest_session()
        if session is None:
            print("no sessions", file=sys.stderr)
            return 2
        matches = [b for b in ledger.batches(session.id, (BatchState.RECOVERY,)) if b.batch_id.startswith(args.batch_id)]
        if len(matches) != 1:
            print(f"no unique delivery needing recovery matches {args.batch_id!r}", file=sys.stderr)
            return 2
        adapter = build_adapter(args.adapter, catalogue, data_dir, sim_file=args.sim_file, save_path=args.save_path,
                                editor_dir=args.editor_dir, steam_manifest=args.steam_manifest)
        bus = ev.EventBus()
        bus.subscribe(lambda e: print(e.message))
        DeliveryEngine(ledger, adapter, catalogue, bus).resolve_recovery(session.id, matches[0].batch_id, args.decision)
    finally:
        ledger.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    data_dir = args.data_dir or default_data_dir()
    catalogue = default_catalogue()

    if args.command == "connect":
        return cmd_connect(args, data_dir, catalogue)
    if args.command == "sim":
        return cmd_sim(args, data_dir, catalogue)
    if args.command == "status":
        return cmd_status(data_dir)
    if args.command == "resolve":
        return cmd_resolve(args, data_dir, catalogue)
    if args.command in (None, "ui"):
        try:
            from .ui.main_window import run_ui
        except ImportError as exc:
            if args.command == "ui":
                print(f"The desktop UI needs PySide6 ({exc}). Install it with: pip install PySide6", file=sys.stderr)
                return 2
            build_parser().print_help()
            return 0
        return run_ui(data_dir, catalogue, adapter_kind=getattr(args, "adapter", "save"),
                      adapter_options={k: getattr(args, k, None) for k in
                                       ("sim_file", "save_path", "editor_dir", "steam_manifest")})
    return 0


if __name__ == "__main__":
    sys.exit(main())
