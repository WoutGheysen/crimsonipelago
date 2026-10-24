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
                                        backup_dir=data_dir / "backups", steam_manifest=steam_manifest,
                                        campaigns_file=data_dir / "campaigns.json")
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
    c.add_argument("--no-live", action="store_true",
                   help="don't give items in the running game; only edit the save once you quit to desktop")
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
    it = sub.add_parser("items", help="look up item keys by name (from your CrimsonSaveEditor folder)")
    it.add_argument("words", nargs="*", help="words in the name, e.g. iron ore")
    it.add_argument("--category", default=None, help="only this category (e.g. Weapon, Material)")
    it.add_argument("--editor-dir", default=None, help="the CrimsonSaveEditor folder (default: the one connect used)")
    pl = sub.add_parser("plugin", help="check the link to the in-game plugin (start the game first)")
    pl.add_argument("action", nargs="?", default="check",
                    choices=["check", "find-copper", "watch-money", "dump-code", "watch-calls", "give-copper",
                             "give-item", "item-table"],
                    help="check: test the link (default); find-copper: research step R1; "
                         "watch-money, dump-code, watch-calls: research step R2; give-copper: R3; "
                         "give-item, item-table: R4")
    pl.add_argument("--key", type=int, default=None, help="give-item: the item's key, as in the save")
    pl.add_argument("--count", type=int, default=1, help="give-item: how many")
    pl.add_argument("--report", default=None,
                    help="dump-code: the watch-money or watch-calls report (default: the newest)")
    pl.add_argument("--port", type=int, default=None)
    pl.add_argument("--pings", type=int, default=5)
    r = sub.add_parser("resolve", help="settle an interrupted delivery")
    r.add_argument("batch_id", help="delivery id (or its first 8 characters)")
    r.add_argument("decision", choices=["delivered", "retry"])
    _add_adapter_args(r)
    return parser


async def run_headless(controller: SyncController, *, watch_plugin: bool = False) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, controller.stop)
        except (NotImplementedError, RuntimeError):  # Windows
            pass
    watcher = asyncio.create_task(watch_game_plugin(controller.bus)) if watch_plugin else None
    try:
        await controller.run()
    finally:
        if watcher:
            watcher.cancel()


async def watch_game_plugin(bus: ev.EventBus, interval: float = 5.0) -> None:
    """Report when the in-game plugin (R0) comes and goes. Nothing depends on it yet."""
    from ..game_adapter.runtime.bridge import BridgeError, GameBridge

    bridge: GameBridge | None = None
    while True:
        try:
            if bridge is None:
                candidate = GameBridge()
                info = await asyncio.to_thread(candidate.connect)
                bridge = candidate
                bus.emit(ev.CONNECTION, f"Game plugin connected: {info.describe()}")
            else:
                await asyncio.to_thread(bridge.ping)
        except BridgeError:
            if bridge is not None:
                bus.emit(ev.CONNECTION, "Game plugin disconnected (game closed?)")
                bridge.close()
                bridge = None
        await asyncio.sleep(interval)


def cmd_connect(args, data_dir: Path, catalogue: Catalogue) -> int:
    settings = load_settings(data_dir)
    server = args.server or settings.get("server")
    slot = args.slot or settings.get("slot_name")
    if not server or not slot:
        print("error: --server and --slot are required the first time", file=sys.stderr)
        return 2
    password = args.password if args.password is not None else os.environ.get("CRIMSONIPELAGO_PASSWORD", "")
    editor_dir = args.editor_dir or settings.get("editor_dir")
    save_settings(data_dir, {**settings, "server": server, "slot_name": slot,
                             **({"editor_dir": editor_dir} if editor_dir else {})})
    if args.adapter == "save" and not editor_dir:
        print("warning: no --editor-dir given, so checks cannot be read from your save", file=sys.stderr)
    adapter = build_adapter(args.adapter, catalogue, data_dir, sim_file=args.sim_file, save_path=args.save_path,
                            editor_dir=editor_dir, steam_manifest=args.steam_manifest)
    ledger = Ledger(data_dir / "ledger.sqlite3")
    bus = ev.EventBus()
    bus.subscribe(lambda e: e.kind != ev.STATUS and print(f"{e.at:%H:%M:%S}  {e.message}", flush=True))
    if not args.verbose:
        logging.getLogger("crimsonipelago.events").setLevel(logging.ERROR)   # printed above already
    config = ClientConfig(server=server, port=args.port, slot_name=slot, password=password, data_dir=data_dir)
    live = None
    if args.adapter == "save" and not args.no_live:
        from ..game_adapter.runtime.live import LiveChannel

        live = LiveChannel(on_event=lambda message: bus.emit(ev.CONNECTION, message))
    controller = SyncController(config, adapter, ledger, catalogue, bus, live=live)
    try:
        # the plugin serves one client at a time: with live delivery on, the channel is that client
        asyncio.run(run_headless(controller, watch_plugin=args.adapter == "save" and live is None))
    except KeyboardInterrupt:
        pass
    finally:
        if live is not None:
            live.close()
        ledger.close()
    if controller.connection and controller.connection.fatal_error:
        print(f"stopped: {controller.connection.fatal_error}", file=sys.stderr)
        return 1
    return 0


NEEDS_PLUGIN = {"find-copper": (0, 3), "watch-money": (0, 4), "dump-code": (0, 5), "watch-calls": (0, 6),
                "give-copper": (0, 8), "give-item": (0, 8), "item-table": (0, 8)}


def _plugin_version(text: str) -> tuple[int, int]:
    try:
        major, minor = text.split(".")[:2]
        return int(major), int(minor)
    except ValueError:
        return (0, 0)


def cmd_plugin(args, data_dir: Path | None = None) -> int:
    import time

    from ..game_adapter.runtime.bridge import DEFAULT_PORT, BridgeError, GameBridge, ResearchBridge

    if args.action in NEEDS_PLUGIN:
        from ..game_adapter.runtime.research import (
            dump_code,
            find_copper,
            give_copper,
            give_item,
            item_table,
            watch_calls,
            watch_money,
        )

        research = ResearchBridge(port=args.port or DEFAULT_PORT)
        try:
            info = research.connect()
            need = NEEDS_PLUGIN[args.action]
            if _plugin_version(info.plugin_version) < need:
                print(f"error: the plugin in the game is {info.plugin_version}; this step needs {need[0]}.{need[1]}.0 "
                      "or newer. Replace crimsonipelago.asi in bin64 with the new one.", file=sys.stderr)
                return 1
            if args.action == "find-copper":
                return 0 if find_copper(research) else 1
            report_dir = (data_dir or default_data_dir()) / "research"
            if args.action == "dump-code":
                reports = sorted(list(report_dir.glob("r2-money-*.json")) + list(report_dir.glob("r2-calls-*.json")),
                                 key=lambda p: p.stat().st_mtime)
                report = Path(args.report) if args.report else (reports[-1] if reports else None)
                if report is None or not report.exists():
                    print("error: no watch-money or watch-calls report found; run one of those first", file=sys.stderr)
                    return 1
                print(f"Using {report}")
                return 0 if dump_code(research, report, report_dir) else 1
            if args.action == "item-table":
                return 0 if item_table(research, report_dir) else 1
            if args.action == "give-item":
                if args.key is None or not 0 < args.count <= 9999:
                    print("error: give-item needs --key KEY (as in the save) and --count 1..9999", file=sys.stderr)
                    return 1
                return 0 if give_item(research, report_dir, args.key, args.count) else 1
            if args.action == "give-copper":
                return 0 if give_copper(research, report_dir) else 1
            if args.action == "watch-calls":
                return 0 if watch_calls(research, report_dir) else 1
            return 0 if watch_money(research, report_dir) else 1
        except BridgeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        except (KeyboardInterrupt, EOFError):
            return 1
        finally:
            research.close()

    bridge = GameBridge(port=args.port or DEFAULT_PORT)
    try:
        info = bridge.connect()
        print("Connected to the game plugin")
        print(f"  plugin version : {info.plugin_version} (protocol {info.protocol})")
        print(f"  game           : {info.exe}")
        print(f"  game version   : {info.exe_version}, build stamp {info.pe_timestamp}")
        print(f"  process        : pid {info.pid}, module at {info.module_base}, {info.module_size:,} bytes")
        times = []
        for _ in range(max(0, args.pings)):
            start = time.perf_counter()
            bridge.ping()
            times.append((time.perf_counter() - start) * 1000)
        if times:
            print(f"  round trip     : {min(times):.2f} ms best, {sum(times) / len(times):.2f} ms average "
                  f"over {len(times)} pings")
        return 0
    except BridgeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("Is the game running, with the ASI loader and crimsonipelago.asi in its bin64 folder? "
              "The plugin writes crimsonipelago_plugin.log next to itself.", file=sys.stderr)
        return 1
    finally:
        bridge.close()


def load_item_list(editor_dir: Path) -> list[dict]:
    """item_names.json from the user's CrimsonSaveEditor folder (game data: read there, never shipped)."""
    import ast

    raw = json.loads((editor_dir / "item_names.json").read_text(encoding="utf-8"))
    items = raw.get("items", raw) if isinstance(raw, dict) else raw
    if isinstance(items, str):                       # the editor stores this list as a Python-literal string
        items = ast.literal_eval(items)
    return [it for it in items if isinstance(it, dict) and "itemKey" in it]


def cmd_items(args, data_dir: Path) -> int:
    editor_dir = args.editor_dir or load_settings(data_dir).get("editor_dir")
    if not editor_dir or not (Path(editor_dir) / "item_names.json").is_file():
        print("error: no CrimsonSaveEditor folder with item_names.json; pass --editor-dir", file=sys.stderr)
        return 1
    words = [w.lower() for w in args.words]
    found = []
    for it in load_item_list(Path(editor_dir)):
        name = str(it.get("name") or it.get("internalName") or "")
        text = f"{name} {it.get('internalName', '')}".lower()
        if all(w in text for w in words) and (not args.category or
                                              str(it.get("category", "")).lower() == args.category.lower()):
            found.append(it)
    for it in found[:40]:
        print(f"{int(it['itemKey']):>10}  {str(it.get('name') or it.get('internalName')):<40} "
              f"{str(it.get('category') or ''):<14} stack {it.get('maxStack', '?')}")
    if len(found) > 40:
        print(f"... and {len(found) - 40} more: add words or --category to narrow it down")
    if not found:
        print("nothing matches")
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
    if args.command == "items":
        return cmd_items(args, data_dir)
    if args.command == "plugin":
        return cmd_plugin(args, Path(data_dir))
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
