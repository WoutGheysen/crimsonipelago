"""The client's link to the in-game plugin, against a stand-in that speaks the plugin's protocol."""

import json
import socket
import threading

import pytest

from crimsonipelago.client.main import main as client_main
from crimsonipelago.game_adapter.runtime.bridge import BridgeError, GameBridge

HELLO = {"type": "hello", "plugin": "crimsonipelago", "plugin_version": "0.1.0-r0", "protocol": 1, "pid": 4242,
         "exe": "C:\\Games\\Crimson Desert\\bin64\\CrimsonDesert.exe", "exe_version": "1.2.3.4",
         "module_base": "0x140000000", "module_size": 123456789, "pe_timestamp": 1792600000, "uptime_ms": 5}


class FakePlugin:
    def __init__(self, hello=HELLO):
        self.hello = hello
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.received = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn:
                conn.sendall((json.dumps(self.hello) + "\n").encode())
                buf = b""
                while True:
                    data = conn.recv(4096)
                    if not data:
                        break
                    buf += data
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        msg = json.loads(line)
                        self.received.append(msg)
                        conn.sendall((json.dumps(self.handle(msg)) + "\n").encode())

    def handle(self, msg):
        if msg.get("cmd") == "ping":
            return {"type": "pong", "id": msg["id"], "uptime_ms": 99}
        if msg.get("cmd") == "info":
            return {**self.hello, "type": "info"}
        return {"type": "error", "message": "unknown command"}

    def close(self):
        self.server.close()


@pytest.fixture
def plugin():
    p = FakePlugin()
    yield p
    p.close()


def test_hello_ping_and_info(plugin):
    with GameBridge(port=plugin.port) as bridge:
        assert bridge.info.exe_version == "1.2.3.4" and bridge.info.pid == 4242
        assert bridge.ping() == 99
        assert bridge.ping() == 99
        assert bridge.refresh_info().pe_timestamp == 1792600000
    assert [m["cmd"] for m in plugin.received] == ["ping", "ping", "info"]
    assert [m["id"] for m in plugin.received[:2]] == [1, 2]


def test_no_plugin_is_a_clear_error():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    with pytest.raises(BridgeError, match="no game plugin"):
        GameBridge(port=port, timeout=0.5).connect()


def test_something_else_on_the_port_is_refused():
    other = FakePlugin(hello={"type": "hello", "plugin": "not-us"})
    try:
        with pytest.raises(BridgeError, match="something else"):
            GameBridge(port=other.port).connect()
    finally:
        other.close()


def test_protocol_mismatch_is_refused():
    other = FakePlugin(hello={**HELLO, "protocol": 99})
    try:
        with pytest.raises(BridgeError, match="protocol 99"):
            GameBridge(port=other.port).connect()
    finally:
        other.close()


def test_plugin_command(plugin, capsys):
    assert client_main(["plugin", "--port", str(plugin.port), "--pings", "3"]) == 0
    out = capsys.readouterr().out
    assert "Connected to the game plugin" in out and "1.2.3.4" in out and "over 3 pings" in out


# --------------------------------------------------------------------------- research (R1)

import struct  # noqa: E402

from crimsonipelago.game_adapter.runtime import research  # noqa: E402
from crimsonipelago.game_adapter.runtime.bridge import ResearchBridge  # noqa: E402
from crimsonipelago.game_adapter.save_edit import ITEM_NO_BASE  # noqa: E402

BASE = 0x10000


class FakeMemory(FakePlugin):
    """A stand-in plugin over 64 KB of memory at 0x10000, with the plugin's search, read and write."""

    def __init__(self):
        self.mem = bytearray(0x10000)
        self.hits, self.width = [], 8
        self.after_rescan = None
        self.after_write = None
        self.watches, self.pending, self.sites, self.unwatched = [], [], {}, 0
        self.on_execute = None          # called with the address when an execute watch is set
        self.next_alloc = 0x1C000
        self.job_msg, self.on_job, self.job_state = None, None, "none"
        self.overlay = {}               # address -> bytes, read before everything else (the game's executable)
        super().__init__()

    def game_writes(self, addr, value, fn, rip, frames):
        """The game changes a value; a watch on it records who did it."""
        self.put(addr, value, 4)
        if addr in self.watches:
            self.sites[rip] = self.sites.get(rip, 0) + 1
            self.pending.append({"slot": self.watches.index(addr), "tid": 7, "value": value, "rip": rip, "fn": fn,
                                 "code": "", "frames": frames, "stack": frames})

    def put(self, addr, value, width=8):
        self.mem[addr - BASE:addr - BASE + width] = value.to_bytes(width, "little")

    def get(self, addr, width=8):
        return int.from_bytes(self.mem[addr - BASE:addr - BASE + width], "little")

    def handle(self, msg):
        cmd = msg["cmd"]
        lo = msg.get("value", msg.get("lo"))
        hi = msg.get("value", msg.get("hi"))
        if cmd in ("scan", "scan_u64"):
            self.width = 8 if cmd == "scan_u64" else msg.get("width", 8)
            align = 4 if cmd == "scan_u64" else msg.get("align", 4)
            pool = range(BASE, BASE + len(self.mem) - self.width + 1, align)
        elif cmd in ("rescan", "rescan_u64"):
            pool = self.hits
        elif cmd == "read":
            start = int(msg["addr"], 16)
            for addr, data in self.overlay.items():
                if addr <= start and start + msg["size"] <= addr + len(data):
                    return {"type": "bytes", "hex": data[start - addr:start - addr + msg["size"]].hex()}
            if start >= 0x140000000:                                  # "code": a recognisable pattern
                return {"type": "bytes", "hex": bytes((start + i) & 0xFF for i in range(msg["size"])).hex()}
            return {"type": "bytes", "hex": self.mem[start - BASE:start - BASE + msg["size"]].hex()}
        elif cmd == "function":
            addr = int(msg["addr"], 16)
            if addr & 0xFFF == 0xBAD:
                return {"type": "function", "begin": "0x0", "end": "0x0"}
            begin = addr & ~0x1FFF
            return {"type": "function", "begin": hex(begin), "end": hex(begin + 0x1800)}
        elif cmd in ("write", "write_u64"):
            addr, width = int(msg["addr"], 16), msg.get("width", 8)
            found = self.get(addr, width)
            if found == msg["expect"]:
                self.put(addr, msg["value"], width)
                if self.after_write:
                    self.after_write(self, addr)
            return {"type": "written", "ok": found == msg["expect"], "found": found}
        elif cmd == "watch":
            if len(self.watches) == 4:
                return {"type": "error", "message": "no free watch"}
            self.watches.append(int(msg["addr"], 16))
            if msg.get("mode") == "execute" and self.on_execute:
                self.on_execute(int(msg["addr"], 16))
            return {"type": "watching", "slot": len(self.watches) - 1, "threads": 12}
        elif cmd == "alloc":
            addr, self.next_alloc = self.next_alloc, self.next_alloc + ((msg["size"] + 0xFF) & ~0xFF)
            return {"type": "alloc", "addr": hex(addr)}
        elif cmd == "poke":
            addr, data = int(msg["addr"], 16), bytes.fromhex(msg["hex"])
            if addr < 0x1C000:
                return {"type": "error", "message": "poke only writes inside the plugin's own allocations"}
            self.mem[addr - BASE:addr - BASE + len(data)] = data
            return {"type": "poked"}
        elif cmd == "job":
            self.job_msg, self.job_state = msg, "queued"
            if self.on_job:
                self.job_state = self.on_job(msg)
            return {"type": "job", "state": "queued", "slot": 0, "threads": 12}
        elif cmd == "job_status":
            return {"type": "job", "state": self.job_state, "rax": "0x0", "code": 0, "tid": 77, "waited_ms": 5}
        elif cmd == "job_cancel":
            self.job_state = "cancelled" if self.job_state == "queued" else self.job_state
            return {"type": "job", "state": self.job_state}
        elif cmd == "watch_apply":
            return {"type": "watching", "slot": -1, "threads": 12}
        elif cmd == "unwatch":
            self.watches, self.unwatched = [], self.unwatched + 1
            return {"type": "unwatched", "threads": 12}
        elif cmd == "hits":
            out, self.pending = self.pending[:msg["max"]], self.pending[msg["max"]:]
            return {"type": "hits", "total": 0, "lost": 0, "records": out,
                    "sites": [{"rip": r, "count": c} for r, c in self.sites.items()]}
        else:
            return super().handle(msg)
        self.hits = [a for a in pool if lo <= self.get(a, self.width) <= hi]
        reply = {"type": "scan", "count": len(self.hits), "truncated": False, "ms": 1,
                 "hits": [hex(a) for a in self.hits[:64]]}
        if cmd.startswith("rescan") and self.after_rescan:
            self.after_rescan(self)
        return reply


def _player(game, script, money, extra=()):
    """Answers the client's questions from `script`; an amount spends money in every place in `money`."""
    answers, said = iter(script), []

    def ask(prompt):
        said.append(prompt)
        answer = next(answers)
        new = research.parse_money(answer) if "amount" in prompt else None
        if new is not None:
            for addr in money:
                game.put(addr, new, 8 if addr not in extra else 4)
        return answer

    return ask, said


def test_find_copper_by_item_number():
    game = FakeMemory()
    copper, number, copy = 0x19000, 0x19000 - 24, 0x15000
    for i in range(10):
        game.put(0x11000 + 16 * i, 800)                           # decoys holding the same amount
    game.put(copper, 800)
    game.put(number, ITEM_NO_BASE + 2)                             # a stack the client added to the save
    game.put(copy, 800)                                            # a copy the game displays
    try:
        ask, said = _player(game, ["8.00", "7.50", "o", "y", "y"], money=(copper, copy))
        with ResearchBridge(port=game.port) as bridge:
            assert research.find_copper(bridge, ask=ask, say=said.append, wait=lambda s: None)
        assert game.get(copper) == 850 and game.get(copy) == 750
        assert all(game.get(0x11000 + 16 * i) == 800 for i in range(10))
        assert any("+24 bytes from item number 900000002" in line for line in said)
        assert any("top right show 8.50" in line for line in said)
        writes = [m for m in game.received if m["cmd"] == "write"]
        assert len(writes) == 1 and writes[0]["width"] == 4
    finally:
        game.close()


def test_find_copper_by_value_puts_back_wrong_guesses():
    game = FakeMemory()
    copper, copy, transient = 0x19000, 0x15000, 0x17000
    for addr in (copper, copy, transient):
        game.put(addr, 800)
    for i in range(10):
        game.put(0x11000 + 16 * i, 800)

    def transient_moves_on(g):
        if g.get(copper) == 700:
            g.put(transient, 0)                                    # a copy that is gone by the time we try it

    game.after_rescan = transient_moves_on
    try:
        # first amount, a change, Enter (no change), another change; then: try the copy (the bag doesn't
        # change), skip nothing, try the real one (it does)
        ask, said = _player(game, ["8.00", "7.50", "", "7.00", "o", "y", "n", "n", "y", "y"], money=(copper, copy, transient))
        with ResearchBridge(port=game.port) as bridge:
            assert research.find_copper(bridge, ask=ask, say=said.append, wait=lambda s: None)
        assert game.get(copper) == 800 and game.get(copy) == 700        # the copy was put back
        assert not any(f"0x{transient:x}" in line for line in said)       # dropped before it was offered
        assert any("Put back" in line for line in said)
        assert any("not in memory" in line for line in said)
    finally:
        game.close()


def test_find_copper_among_copies_finds_the_one_the_others_follow():
    game = FakeMemory()
    places = [0x11000 + 0x100 * i for i in range(7)]               # what the game showed: 7 places follow
    source = places[2]
    for addr in places:
        game.put(addr, 800)

    def copy_from_source(g, addr):
        if addr == source:
            for other in places:
                g.put(other, g.get(source))

    game.after_write = copy_from_source
    try:
        ask, said = _player(game, ["8.00", "7.00", "6.00", "o", "y", "n", "y", "y", "n", "n", "y", "y"], money=places)
        with ResearchBridge(port=game.port) as bridge:
            assert research.find_copper(bridge, ask=ask, say=said.append, wait=lambda s: None)
        assert all(game.get(a) == 700 for a in places)
        assert any("Found 7 candidate(s)" in line for line in said)
        assert any("6 of the other places changed to 700" in line for line in said)
        assert any(f"lives at 0x{source:x}" in line and "6 other place(s) followed" in line for line in said)
    finally:
        game.close()


def test_find_copper_can_change_every_place_at_once():
    game = FakeMemory()
    places = [0x11000 + 0x100 * i for i in range(9)]
    for addr in places:
        game.put(addr, 800)
    try:
        ask, said = _player(game, ["8.00", "7.00", "6.00", "a", "y", "y"], money=places)
        with ResearchBridge(port=game.port) as bridge:
            assert research.find_copper(bridge, ask=ask, say=said.append, wait=lambda s: None)
        assert all(game.get(a) == 700 for a in places)
        assert any("Wrote 700 to 9 of 9 places" in line for line in said)
    finally:
        game.close()


def test_all_at_once_is_put_back_when_nothing_shows():
    game = FakeMemory()
    places = [0x11000 + 0x100 * i for i in range(3)]
    for addr in places:
        game.put(addr, 800)
    try:
        ask, said = _player(game, ["8.00", "7.00", "6.00", "a", "n", "n"], money=places)
        with ResearchBridge(port=game.port) as bridge:
            assert not research.find_copper(bridge, ask=ask, say=said.append, wait=lambda s: None)
        assert all(game.get(a) == 600 for a in places)
    finally:
        game.close()


def test_watch_money_records_who_changes_it(tmp_path):
    game = FakeMemory()
    record, total = 0x19010, 0x15000
    game.put(record - 16, ITEM_NO_BASE + 2)
    for addr in (record, total):
        game.put(addr, 800, 4)
    shop = ["CrimsonDesert.exe+0x500", "CrimsonDesert.exe+0x900"]
    actions = iter([
        lambda: [game.game_writes(a, 650, "CrimsonDesert.exe+0x100", "CrimsonDesert.exe+0x123", shop)
                 for a in (record, total)],                                            # buy
        lambda: [game.game_writes(record, 655 + i, "CrimsonDesert.exe+0x100", "CrimsonDesert.exe+0x123", shop)
                 for i in range(70)],                                                  # many writes: drained in pages
        lambda: None,
    ])
    said = []
    answers = iter(["8.00", "7.00", "buy", "sell", "d"])

    def ask(prompt):
        said.append(prompt)
        answer = next(answers)
        if "amount" in prompt and answer:
            for addr in (record, total):
                game.put(addr, research.parse_money(answer), 4)
        if "What did you just do" in prompt:
            next(actions)()
        return answer

    try:
        with ResearchBridge(port=game.port) as bridge:
            path = research.watch_money(bridge, tmp_path, ask=ask, say=said.append, wait=lambda s: None)
        assert path is not None and path.exists()
        report = json.loads(path.read_text())
        assert len(report["records"]) == 72 and report["sites"] == {"CrimsonDesert.exe+0x123": 72}
        assert [w["addr"] for w in report["watched"]] == [hex(record), hex(total)]
        assert report["events"] == ["buy", "sell"]
        assert {r["event"] for r in report["records"][:2]} == {"buy"} and report["records"][-1]["event"] == "sell"
        assert game.unwatched == 1 and game.watches == []
        assert any("CrimsonDesert.exe+0x100: 72 write(s)" in line for line in said)
        assert any("called from CrimsonDesert.exe+0x500 <- CrimsonDesert.exe+0x900" in line for line in said)
    finally:
        game.close()


def test_watch_money_needs_the_new_plugin(capsys):
    game = FakeMemory()
    try:
        assert client_main(["plugin", "watch-money", "--port", str(game.port)]) == 1
        assert "needs 0.4.0" in capsys.readouterr().err
    finally:
        game.close()


def test_dump_code_saves_each_function_once(tmp_path):
    game = FakeMemory()
    report = tmp_path / "r2-money-1.json"
    report.write_text(json.dumps({
        "game": {"pe_timestamp": HELLO["pe_timestamp"]},
        "sites": {"CrimsonDesert.exe+0x4484": 4},
        "records": [{"rip": "CrimsonDesert.exe+0x4484", "fn": "CrimsonDesert.exe+0x4000",
                     "frames": ["CrimsonDesert.exe+0x6150", "CrimsonDesert.exe+0x7bad", "ntdll.dll+0x10"]}],
    }))
    said = []
    try:
        with ResearchBridge(port=game.port) as bridge:
            path = research.dump_code(bridge, report, tmp_path, say=said.append)
        dump = json.loads(path.read_text())
        by_start = {f["rva_begin"]: f for f in dump["functions"]}
        assert set(by_start) == {"0x4000", "0x6000", hex(0x7BAD - 512)}
        assert by_start["0x4000"]["refs"] == ["0x4484", "0x4000"]
        assert len(by_start["0x4000"]["bytes"]) == 2 * 0x1800 and not by_start["0x4000"]["truncated"]
        assert by_start[hex(0x7BAD - 512)]["kind"].startswith("no unwind data")
        start = 0x140000000 + 0x6000
        assert bytes.fromhex(by_start["0x6000"]["bytes"])[:4] == bytes((start + i) & 0xFF for i in range(4))
    finally:
        game.close()


def test_dump_code_refuses_a_report_from_another_build(tmp_path):
    game = FakeMemory()
    report = tmp_path / "r2-money-1.json"
    report.write_text(json.dumps({"game": {"pe_timestamp": 1}, "records": []}))
    said = []
    try:
        with ResearchBridge(port=game.port) as bridge:
            assert research.dump_code(bridge, report, tmp_path, say=said.append) is None
        assert any("another game build" in line for line in said)
    finally:
        game.close()


def _targets(tmp_path, stamp):
    path = tmp_path / "targets.json"
    path.write_text(json.dumps({"builds": {str(stamp): {"watch_calls": [
        {"rva": "0x1000", "name": "transaction: add", "follow": "5:0:896"},
        {"rva": "0x2000", "name": "transaction: commit"}]}}}))
    return path


def test_watch_calls_labels_the_calls_with_what_the_player_did(tmp_path):
    game = FakeMemory()

    def loot():
        for slot in (0, 1):
            game.pending.append({"slot": slot, "tid": 7, "value": 0, "rip": f"CrimsonDesert.exe+0x{slot + 1}000",
                                 "fn": "", "code": "", "frames": [], "stack": [], "args": ["0x1", "0x2"],
                                 "memory": {"0": "00ff"}})

    actions = iter([loot, loot, lambda: None])
    said = []
    answers = iter(["loot", "loot", "d"])

    def ask(prompt):
        said.append(prompt)
        next(actions)()
        return next(answers)

    try:
        with ResearchBridge(port=game.port) as bridge:
            path = research.watch_calls(bridge, tmp_path, ask=ask, say=said.append,
                                        targets_file=_targets(tmp_path, HELLO["pe_timestamp"]))
        report = json.loads(path.read_text())
        assert [r["function"] for r in report["records"]] == ["transaction: add", "transaction: commit"] * 2
        assert report["events"] == ["loot", "loot #2"]
        assert [r["event"] for r in report["records"]] == ["loot", "loot", "loot #2", "loot #2"]
        assert [m["addr"] for m in game.received if m["cmd"] == "watch"] == [hex(0x140001000), hex(0x140002000)]
        assert all(m["mode"] == "execute" for m in game.received if m["cmd"] == "watch")
        assert [m.get("follow") for m in game.received if m["cmd"] == "watch"] == ["5:0:896", None]
        assert all(m["max"] == research.CALL_PAGE for m in game.received if m["cmd"] == "hits")
        assert any("  loot: transaction: add x1, transaction: commit x1" in line for line in said)
        assert any("  loot #2: transaction: add x1, transaction: commit x1" in line for line in said)
        assert game.unwatched == 1
    finally:
        game.close()


def test_watch_calls_refuses_an_unknown_build(tmp_path):
    game = FakeMemory()
    said = []
    try:
        with ResearchBridge(port=game.port) as bridge:
            assert research.watch_calls(bridge, tmp_path, ask=lambda p: "d", say=said.append,
                                        targets_file=_targets(tmp_path, 1)) is None
        assert any("don't know this game build" in line for line in said)
        assert not [m for m in game.received if m["cmd"] == "watch"]
    finally:
        game.close()


def test_shipped_targets_cover_the_researched_build():
    build = research.load_targets(1790086912)
    assert build and len(build["watch_calls"]) == 4
    for f in build["watch_calls"]:
        for spec in filter(None, f.get("follow", "").split(",")):
            arg, offset, size = (int(x, 0) for x in spec.split(":"))
            assert 0 <= arg < 16 and 0 <= offset <= 4096 and 0 < size <= 2048
    assert all(int(rva, 16) for rva in build["dump_extra"])


TX, OWNER, LOGIC_TID = 0x1A000, 0x1A800, 77


def _r3_game(tmp_path):
    """A fake game with a transaction, its owner, the game-logic thread and two Copper stack records."""
    game = FakeMemory()
    game.put(TX, OWNER)
    for addr, side in ((0x18000, 0), (0x18400, 0xFFFFFFFF)):          # the screen side, the game-logic side
        game.put(addr, ITEM_NO_BASE + 2)
        game.put(addr + 8, 0x7BC, 4)
        game.put(addr + 12, side, 4)
        game.put(addr + 0x10, 468)
        game.put(addr + 0x20, 0x1A900)                                  # something pointer-like inside

    def background_add(addr):
        game.pending.append({"slot": 0, "tid": LOGIC_TID, "value": 0, "rip": hex(addr), "fn": "", "code": "",
                             "frames": ["CrimsonDesert.exe+0x2b753f6"], "stack": [],
                             "args": [hex(TX), "0x1", hex(OWNER), "0x2", "0x3", "0x4", "0x5", "0x6", "0x7",
                                      hex(0x6ADC053D), hex(0x18CEBEF0), "0x0", "0x0", "0x0", "0x0", "0x0"]})

    game.on_execute = background_add
    targets = tmp_path / "targets.json"
    targets.write_text(json.dumps({"builds": {str(HELLO["pe_timestamp"]): {
        "r3": {"probe": "0x277e080", "give": "0x2781780", "runner": "0x2774b20", "copper_handle": "0x7bc",
               "reason": "0x0b"}, "dump_extra": []}}}))
    return game, targets


def test_give_copper_calls_the_game_with_a_kind_0_entry(tmp_path):
    game, targets = _r3_game(tmp_path)

    def the_game_commits(msg):
        args = [int(a, 16) for a in msg["args"].split(",")]
        tx, result, owner, none, adds = args[:5]
        assert (tx, owner) == (TX, OWNER) and msg["tid"] == LOGIC_TID
        assert int(msg["fn"], 16) == 0x140000000 + 0x2781780 and int(msg["runner"], 16) == 0x140000000 + 0x2774b20
        assert msg["set"] == f"{hex(TX + 0x1AB)}=0"
        assert args[5:] == [1, args[6], 0x18CEBEF0, 0x0B, 0, 0, 0xFFFF, 1, 1]
        assert game.get(none) == 0 and game.get(none + 8, 4) == 0
        entry, count = game.get(adds), game.get(adds + 8, 4)
        assert count == 1 and game.get(entry, 1) == 0 and game.get(entry + 8) == 2**64 - 1
        item = game.get(entry + 0x30)
        assert game.get(item + 8, 2) == 0x7BC and game.get(item) >= research.LIVE_ITEM_NO_BASE
        game.put(0x18400 + 0x10, game.get(0x18400 + 0x10) + game.get(item + 0x10))   # the commit merges it
        game.put(result, 0, 4)
        return "done"

    game.on_job = the_game_commits
    answers = iter(["y", "y", "y", "y", "n"])
    said = []
    try:
        with ResearchBridge(port=game.port) as bridge:
            path = research.give_copper(bridge, tmp_path, ask=lambda p: (said.append(p), next(answers))[1],
                                        say=said.append, wait=lambda s: None, targets_file=targets)
        report = json.loads(path.read_text())
        assert report["outcome"] == "accepted" and report["result_code"] == 0
        assert report["record"]["addr"] == hex(0x18400)                     # the game-logic side's copy
        assert report["count_after"] == 568 and report["record"]["count_before"] == 468
        assert report["record"]["pointer_like_fields"] == ["0x20"]
        assert report["player"] == {"bag": True, "top_right": True, "popup": False}
        assert any("from 4.68 to 5.68 silver" in line for line in said)
        assert game.unwatched >= 2                                          # the probe and the runner removed
    finally:
        game.close()


def test_give_copper_reports_a_refusal_and_a_missing_stack(tmp_path):
    game, targets = _r3_game(tmp_path)
    game.on_job = lambda msg: "done"                                       # result stays 0xFFFFFFFF
    try:
        with ResearchBridge(port=game.port) as bridge:
            path = research.give_copper(bridge, tmp_path, ask=lambda p: "y", say=lambda s: None,
                                        wait=lambda s: None, targets_file=targets)
        assert json.loads(path.read_text())["outcome"] == "refused"
        game.put(0x18400 + 8, 0x123, 4)
        game.put(0x18000 + 8, 0x123, 4)                                    # no Copper stack any more
        with ResearchBridge(port=game.port) as bridge:
            path = research.give_copper(bridge, tmp_path, ask=lambda p: "y", say=lambda s: None,
                                        wait=lambda s: None, targets_file=targets)
        assert json.loads(path.read_text())["outcome"] == "no copper stack"
    finally:
        game.close()


def test_give_copper_stops_without_the_players_go_ahead(tmp_path):
    game, targets = _r3_game(tmp_path)
    try:
        with ResearchBridge(port=game.port) as bridge:
            assert research.give_copper(bridge, tmp_path, ask=lambda p: "n", say=lambda s: None,
                                        wait=lambda s: None, targets_file=targets) is None
        assert game.job_msg is None and not [m for m in game.received if m["cmd"] == "watch"]
    finally:
        game.close()


def _r4_game(tmp_path, *, column_right=True):
    """_r3_game plus the game's item table: 3 items, Copper (key 1, handle 0x7BC in the real game; here 1),
    Abyss Artifact (75001, handle 2) and an unloaded one (12345, handle 0)."""
    game, targets = _r3_game(tmp_path)
    table, keys, data = 0x1B000, 0x1B100, 0x1B200
    game.overlay[0x140000000 + 0x6D69AA8] = table.to_bytes(8, "little")
    game.put(table + 8, 3, 4)
    game.put(table + 0x28, keys)
    game.put(table + 0x58, data)
    for handle, key in ((0, 12345), (1, 1), (2, 75001)):
        game.put(keys + 8 * handle, key if column_right else key + 1, 4)
    for handle, key in ((1, 1), (2, 75001)):                      # loaded item data: the key first
        game.put(data + 8 * handle, 0x1B400 + 0x10 * handle)
        game.put(0x1B400 + 0x10 * handle, key, 4)
    for addr in (0x18000, 0x18400):                                # the Copper records use handle 1 here
        game.put(addr + 8, 1, 2)
    t = json.loads(targets.read_text())
    t["builds"][str(HELLO["pe_timestamp"])]["r3"].update({"item_table": "0x6d69aa8", "copper_handle": "0x1"})
    targets.write_text(json.dumps(t))
    return game, targets


def test_item_table_maps_keys_to_handles(tmp_path):
    game, targets = _r4_game(tmp_path)
    try:
        with ResearchBridge(port=game.port) as bridge:
            table = research.read_item_table(bridge, 0x140000000, 0x6D69AA8)
        assert table.handles == {12345: 0, 1: 1, 75001: 2} and table.source == "keys"
        assert (table.count, table.loaded, table.checked) == (3, 2, 2)
    finally:
        game.close()


def test_item_table_falls_back_to_loaded_items_when_the_column_disagrees(tmp_path):
    game, targets = _r4_game(tmp_path, column_right=False)
    try:
        with ResearchBridge(port=game.port) as bridge:
            table = research.read_item_table(bridge, 0x140000000, 0x6D69AA8)
        assert table.handles == {1: 1, 75001: 2} and table.source == "loaded" and table.checked == 0
    finally:
        game.close()


def test_give_item_uses_the_handle_and_a_copper_template(tmp_path):
    game, targets = _r4_game(tmp_path)
    seen = {}

    def the_game_commits(msg):
        args = [int(a, 16) for a in msg["args"].split(",")]
        item = game.get(game.get(args[4]) + 0x30)
        seen.update(handle=game.get(item + 8, 2), count=game.get(item + 0x10))
        game.put(args[1], 0, 4)
        return "done"

    game.on_job = the_game_commits
    try:
        with ResearchBridge(port=game.port) as bridge:
            path = research.give_item(bridge, tmp_path, 75001, 2, ask=lambda p: "y", say=lambda s: None,
                                      wait=lambda s: None, targets_file=targets, label="Abyss Artifact")
        report = json.loads(path.read_text())
        assert report["outcome"] == "accepted" and seen == {"handle": 2, "count": 2}
        assert report["give"]["template_same_item"] is False and report["table"]["source"] == "keys"
        scans = [m for m in game.received if m["cmd"] == "scan" and m["width"] == 8]
        assert scans and all(m["hi"] - m["lo"] < 1000 for m in scans)          # narrow: first 64 hits only
        assert game.watches == []                                         # runner removed after the call
    finally:
        game.close()


def test_give_item_refuses_an_unknown_key(tmp_path):
    game, targets = _r4_game(tmp_path)
    try:
        with ResearchBridge(port=game.port) as bridge:
            path = research.give_item(bridge, tmp_path, 99999, 1, ask=lambda p: "y", say=lambda s: None,
                                      wait=lambda s: None, targets_file=targets)
        assert json.loads(path.read_text())["outcome"] == "unknown item" and game.job_msg is None
    finally:
        game.close()


def test_live_channel_gives_with_a_built_in_record(tmp_path):
    from crimsonipelago.game_adapter.runtime.live import LiveChannel, stackable_record

    game, targets = _r4_game(tmp_path)
    game.hello = {**HELLO, "plugin_version": "0.8.0-r3"}
    seen = {}

    def the_game_commits(msg):
        args = [int(a, 16) for a in msg["args"].split(",")]
        item = game.get(game.get(args[4]) + 0x30)
        seen.update(handle=game.get(item + 8, 2), count=game.get(item + 0x10), tid=msg["tid"],
                    copy=game.get(item + 0x90, 2))
        game.put(args[1], 0 if seen["handle"] != 0 else 7, 4)          # the game refuses handle 0
        return "done"

    game.on_job = the_game_commits
    events = []
    live = LiveChannel(game.port, on_event=events.append, targets_file=targets, sleep=lambda s: None)
    try:
        game.on_execute, background = None, game.on_execute
        first = live.give(75001, 2)
        assert first.state == "not now" and "waiting for the game" in first.message   # watch armed, nothing yet
        background(0x140000000 + 0x277e080)                             # the game makes an "add" of its own
        second = live.give(75001, 2)
        assert second.given and seen == {"handle": 2, "count": 2, "tid": LOGIC_TID, "copy": 2}
        game.on_execute = background
        assert any("items arrive while you play" in e for e in events)
        assert live.give(99999, 1).state == "unknown item"
        live.probe = None
        live.poll()                                                     # a client tick arms the watch again
        refused = live.give(12345, 1)
        assert refused.state == "refused" and refused.code == 7
        assert live.give(12345, 1).state == "refused"                  # not tried again
        assert len([m for m in game.received if m["cmd"] == "job"]) == 2
        rec = stackable_record(0x4D1, 3, 905000001, 1792869000)
        assert len(rec) == 0xC8 and rec[0x90:0x92] == (0x4D1).to_bytes(2, "little")
    finally:
        live.close()
        game.close()


def test_live_channel_learns_knowledge_with_a_kind_4_entry(tmp_path):
    from crimsonipelago.game_adapter.runtime.live import LiveChannel

    game, targets = _r4_game(tmp_path)
    game.hello = {**HELLO, "plugin_version": "0.8.0-r3"}
    seen = {}

    def the_game_commits(msg):
        args = [int(a, 16) for a in msg["args"].split(",")]
        entry = game.get(args[4])
        seen.update(kind=game.get(entry, 1), key=game.get(entry + 8, 4), level=game.get(entry + 12, 4),
                    learn_from=game.get(entry + 16, 1), count=game.get(args[4] + 8, 4))
        game.put(args[1], 0, 4)
        return "done"

    game.on_job = the_game_commits
    live = LiveChannel(game.port, targets_file=targets, sleep=lambda s: None)
    try:
        result = live.give_knowledge(40002)
        assert result.given and seen == {"kind": 4, "key": 40002, "level": 1, "learn_from": 25, "count": 1}
    finally:
        live.close()
        game.close()


def test_live_channel_stays_off_for_an_old_plugin_or_unknown_build(tmp_path):
    from crimsonipelago.game_adapter.runtime.live import LiveChannel

    game, targets = _r4_game(tmp_path)                                  # HELLO says 0.1.0-r0
    events = []
    live = LiveChannel(game.port, on_event=events.append, targets_file=targets)
    try:
        assert not live.ready() and "needs 0.8.0" in live.unsupported
        result = live.give(1, 100)
        assert result.state == "not now" and game.job_msg is None
    finally:
        live.close()
        game.close()
    game, targets = _r4_game(tmp_path)
    game.hello = {**HELLO, "plugin_version": "0.8.0-r3", "pe_timestamp": 5}
    live = LiveChannel(game.port, on_event=events.append, targets_file=targets)
    try:
        assert not live.ready() and "doesn't know this game version" in live.unsupported
    finally:
        live.close()
        game.close()


def test_build_gift_layout():
    record = bytes(range(256))[:research.RECORD_SIZE]
    block, layout = research.build_gift(record, 100, 905_000_001, 0x5000)
    assert struct.unpack_from("<QQ", block, 0x10)[0] == 100 and struct.unpack_from("<Q", block, 0)[0] == 905_000_001
    assert block[8:16] == record[8:16]                                      # key handle kept
    assert struct.unpack_from("<Q", block, 0x130)[0] == 0x5000                 # entry +0x30 -> item
    assert struct.unpack_from("<QII", block, 0x140) == (0x5100, 1, 1)
    assert struct.unpack_from("<I", block, 0x180)[0] == 0xFFFFFFFF


def test_find_copper_gives_up_cleanly_when_nothing_follows():
    game = FakeMemory()
    game.put(0x11000, 800)
    try:
        ask, said = _player(game, ["8.00", "7.50"], money=())
        with ResearchBridge(port=game.port) as bridge:
            assert not research.find_copper(bridge, ask=ask, say=said.append, wait=lambda s: None)
        assert any("Nothing follows your money" in line for line in said)
    finally:
        game.close()


def test_eight_byte_commands_still_work():
    game = FakeMemory()
    game.put(0x12000, 0xC0FFEE)
    try:
        with ResearchBridge(port=game.port) as bridge:
            assert bridge.scan_u64(0xC0FFEE).hits == [0x12000]
            assert bridge.rescan_u64(0xC0FFEE).count == 1
            assert bridge.write_u64(0x12000, 1, 2) == (False, 0xC0FFEE)
            assert bridge.write_u64(0x12000, 0xC0FFEE, 7) == (True, 0xC0FFEE)
            assert bridge.read_int(0x12000) == 7
    finally:
        game.close()


def test_find_copper_asks_for_the_new_plugin(capsys):
    game = FakeMemory()                                            # says it is 0.1.0-r0
    try:
        assert client_main(["plugin", "find-copper", "--port", str(game.port)]) == 1
        assert "needs 0.3.0" in capsys.readouterr().err
    finally:
        game.close()


def test_money_is_read_as_shown():
    assert research.parse_money("8.00") == 800
    assert research.parse_money("8,5") == 850
    assert research.parse_money("8") == 800                         # silver, as the bag shows it
    assert research.parse_money("5,70") == 570
    assert research.parse_money("1,234.56") == 123456
    assert research.parse_money("-1") is None
    assert research.parse_money("abc") is None
    assert research.shown(570) == "5.70"


def test_items_lookup_reads_the_editor_list(tmp_path, capsys):
    editor = tmp_path / "editor"
    editor.mkdir()
    rows = [{"itemKey": 1, "name": "Copper", "category": "Currency", "maxStack": 100000},
            {"itemKey": 75001, "name": "Abyss Artifact", "category": "Material", "maxStack": 10},
            {"itemKey": 4242, "name": "Iron Sword", "category": "Weapon", "maxStack": 1}]
    (editor / "item_names.json").write_text(json.dumps({"items": repr(rows)}))      # the editor's literal form
    assert client_main(["--data-dir", str(tmp_path), "items", "sword", "--editor-dir", str(editor)]) == 0
    out = capsys.readouterr().out
    assert "4242" in out and "Iron Sword" in out and "Copper" not in out
    assert client_main(["--data-dir", str(tmp_path), "items", "--category", "material", "--editor-dir", str(editor)]) == 0
    assert "75001" in capsys.readouterr().out


def test_knowledge_lookup_follows_the_checkers_hash_map(tmp_path):
    game, targets = _r4_game(tmp_path)
    table, buckets, records = 0x1C800, 0x1D000, 0x1CF00
    game.overlay[0x140000000 + 0x6D69A98] = table.to_bytes(8, "little")
    game.put(table + 0x68, 4, 4)                        # 4 buckets
    game.put(table + 0x6C, 2, 4)                        # 2 used
    game.put(table + 0x78, buckets)
    game.put(table + 0x80, records)
    key = 1005839                                       # bucket 1005839 % 4 = 3
    game.put(buckets + 3 * 0x100, 1, 4)
    game.put(buckets + 3 * 0x100 + 8, key, 4)
    game.put(buckets + 3 * 0x100 + 12, 1, 4)            # record index 1
    game.put(records + 8, 0x1CE00)
    game.put(0x1CE00 + 4, key, 4)
    game.put(0x1CE00 + 8, 0x1234, 2)
    try:
        with ResearchBridge(port=game.port) as bridge:
            found = research.knowledge_lookup(bridge, 0x140000000, 0x6D69A98, key)
            missing = research.knowledge_lookup(bridge, 0x140000000, 0x6D69A98, 40002)
        assert found["handle"] == 0x1234 and found["index"] == 1
        assert missing["problem"] == "not in the table" and "handle" not in missing
    finally:
        game.close()


def test_error_sites_finds_the_loads_of_a_result_code(tmp_path):
    game = FakeMemory()
    # mov eax, [rip+0x100] at +0x1000: the global is +0x1000 + 6 + 0x100; mov r8d, [rip+0x200] at +0x1010
    code = bytearray(0x20)
    code[0:6] = bytes([0x8B, 0x05]) + (0x100).to_bytes(4, "little")
    code[0x10:0x17] = bytes([0x44, 0x8B, 0x05]) + (0x200).to_bytes(4, "little")
    (tmp_path / "r2-code-1.json").write_text(json.dumps({"game": {"pe_timestamp": HELLO["pe_timestamp"]},
                                                         "functions": [{"rva_begin": "0x1000", "bytes": code.hex()}]}))
    game.overlay[0x140000000 + 0x1106] = (0x78C0A4E9).to_bytes(4, "little")
    game.overlay[0x140000000 + 0x1217] = (0x1234).to_bytes(4, "little")
    try:
        with ResearchBridge(port=game.port) as bridge:
            path = research.error_sites(bridge, tmp_path, 0x78C0A4E9, say=lambda s: None)
        sites = json.loads(path.read_text())["sites"]
        assert sites == [{"site": "0x1000", "global": "0x1106", "function": "0x1000"}]
    finally:
        game.close()


def test_live_channel_logs_deliveries_refusals_and_transaction_changes(tmp_path):
    from crimsonipelago.game_adapter.runtime.live import JsonlLog, LiveChannel

    game, targets = _r4_game(tmp_path)
    game.hello = {**HELLO, "plugin_version": "0.8.1-r5"}
    game.put(TX + 0x10, 0xABCDEF)                                       # something to see in the refusal

    def the_game_commits(msg):
        args = [int(a, 16) for a in msg["args"].split(",")]
        item = game.get(game.get(args[4]) + 0x30)
        game.put(args[1], 0 if game.get(item + 8, 2) != 0 else 7, 4)    # the game refuses handle 0
        return "done"

    game.on_job = the_game_commits
    log = JsonlLog(tmp_path / "logs")
    live = LiveChannel(game.port, targets_file=targets, sleep=lambda s: None, log=log)
    try:
        assert live.give(75001, 2).given
        assert live.give(12345, 1).state == "refused"
        background = game.on_execute

        def another_owner(addr):                                        # a save loaded: a new owner, clock back
            background(addr)
            hit = game.pending[-1]
            hit["args"][2], hit["args"][10] = hex(OWNER + 0x100), hex(0x100)
        game.on_execute = another_owner
        live.poll()
        live.poll()
    finally:
        live.close()
        game.close()
    lines = [json.loads(line) for f in (tmp_path / "logs").glob("live-*.jsonl") for line in f.read_text().splitlines()]
    events = [r["event"] for r in lines]
    assert events[0] == "connected" and events.count("give") == 2
    gives = [r for r in lines if r["event"] == "give"]
    assert gives[0]["state"] == "given" and "tx_before" not in gives[0]
    assert gives[1]["state"] == "refused" and gives[1]["code"] == 7 and "efcdab" in gives[1]["tx_before"]
    changed = [r for r in lines if r["event"] == "transaction" and "changed" in r]
    assert changed and set(changed[-1]["changed"]) == {"owner", "clock went back"}
    assert len(live.sightings) >= 3 and all("clock" in s for s in live.sightings)


def test_edge_cases_gives_silver_around_each_step_and_records_the_transaction(tmp_path):
    from crimsonipelago.game_adapter.runtime.edge_cases import Step, edge_cases

    game, targets = _r4_game(tmp_path)
    game.hello = {**HELLO, "plugin_version": "0.8.1-r5"}
    given = []

    def the_game_commits(msg):
        args = [int(a, 16) for a in msg["args"].split(",")]
        item = game.get(game.get(args[4]) + 0x30)
        given.append((game.get(item + 8, 2), game.get(item + 0x10)))
        game.put(args[1], 0, 4)
        return "done"

    game.on_job = the_game_commits
    steps = (Step("standing", "Stand still."),
             Step("menu", "Open the map.", during=True, questions=("Arrived while open?",)),
             Step("reload", "Load your save.", before=True, questions=("Still there?",)))
    answers = {"Still there?": "n", "Arrived while open?": "n"}
    said = []
    try:
        with ResearchBridge(port=game.port) as bridge:
            path = edge_cases(bridge, tmp_path, say=said.append, wait=lambda s: None, targets_file=targets,
                              ask=lambda p: next((a for q, a in answers.items() if q in p), "y"),
                              steps=steps, background=False)
        report = json.loads(path.read_text())
    finally:
        game.close()
    assert report["outcome"] == "done" and [s["step"] for s in report["steps"]] == ["standing", "menu", "reload"]
    assert given == [(1, 100)] * 5                                      # after each, during the menu, before reload
    menu, reload = report["steps"][1], report["steps"][2]
    assert menu["during"]["state"] == "given" and menu["answers"] == {"Arrived while open?": False}
    assert reload["before"]["arrived"] is True and reload["answers"] == {"Still there?": False}
    assert reload["after"]["state"] == "given" and reload["transaction"]["count"] >= 1
    assert reload["transaction"]["owners"] == [hex(OWNER)]
    assert any("Saved:" in line for line in said)


def test_edge_cases_stops_without_the_players_go_ahead(tmp_path):
    from crimsonipelago.game_adapter.runtime.edge_cases import edge_cases

    game, targets = _r4_game(tmp_path)
    game.hello = {**HELLO, "plugin_version": "0.8.1-r5"}
    try:
        with ResearchBridge(port=game.port) as bridge:
            assert edge_cases(bridge, tmp_path, ask=lambda p: "n", say=lambda s: None, targets_file=targets,
                              background=False) is None
        assert game.job_msg is None
    finally:
        game.close()


def test_live_channel_counts_a_load_by_owner_or_clock_but_not_a_death():
    from crimsonipelago.game_adapter.runtime.live import LiveChannel
    from crimsonipelago.game_adapter.runtime.research import Probe

    live, logged = LiveChannel(log=None), []
    live.log = logged.append
    tx, owner = 0x2C7BF7B24C0, 0x2C7CA0E0200

    def seen(owner, clock, unix=1792880000):
        live._sighted(Probe(tx, owner, 20716, unix, clock, "CrimsonDesert.exe+0x2b753f6"))

    seen(owner, 459001056)
    seen(owner, 459100000)                          # the horse, fast travel, dying: the clock goes on
    assert live.loads == 0 and live.last_seen_unix == 1792880000
    seen(owner + 0x200, 448900260, 1792880454)      # loading the manual save: new owner, clock back
    assert live.loads == 1 and live.load_at > 0 and live.last_seen_unix == 1792880454
    seen(owner + 0x200, 448901208)
    seen(owner + 0x200, 448800000)                  # the clock back alone
    assert live.loads == 2
    seen(owner + 0x200, 0xFFFFFF00)
    seen(owner + 0x200, 0x100)                      # a 32-bit clock wrapping round is not a load
    assert live.loads == 2
    seen(owner + 0x400, 0x200)                      # a new owner alone
    assert live.loads == 3
    assert [r["event"] for r in logged].count("load") == 3


def test_live_channel_waits_for_the_games_own_add_after_a_run_of_items_then_goes_on(tmp_path):
    from crimsonipelago.game_adapter.runtime.live import LiveChannel

    game, targets = _r4_game(tmp_path)
    game.hello = {**HELLO, "plugin_version": "0.8.1-r5"}
    calls = []

    def the_game_takes_two_in_a_row(msg):
        args = [int(a, 16) for a in msg["args"].split(",")]
        calls.append(1)
        game.put(args[1], 0x40128A9B if len(calls) == 3 else 0, 4)   # the third in a row: "not now"
        return "done"

    game.on_job = the_game_takes_two_in_a_row
    background, game.on_execute = game.on_execute, None              # the game's own adds come when we say
    logged = []
    live = LiveChannel(game.port, targets_file=targets, sleep=lambda s: None, log=logged.append)
    try:
        live.give(1, 100)
        background(0x140000000 + 0x277e080)
        assert live.give(1, 100).given and live.give(1, 100).given
        turned_down = live.give(1, 100)
        assert turned_down.state == "not now" and "turned it down for now" in turned_down.message
        waiting = live.give(1, 100)
        assert waiting.state == "not now" and "after its next transaction" in waiting.message and len(calls) == 3
        assert live.give(75001, 1).state == "not now" and len(calls) == 3   # nothing else either, meanwhile
        background(0x140000000 + 0x277e080)                             # the game makes an add of its own
        assert live.give(1, 100).given and len(calls) == 4
        assert 1 not in live.refused
    finally:
        live.close()
        game.close()
    gives = [r for r in logged if r["event"] == "give"]
    assert [r["state"] for r in gives].count("not now") <= 4             # repeated waits logged once
    assert any(r["event"] == "seen" for r in logged)


def test_live_channel_gives_up_on_an_item_turned_down_again_and_again(tmp_path):
    from crimsonipelago.game_adapter.runtime.live import TRANSIENT_TRIES, LiveChannel

    game, targets = _r4_game(tmp_path)
    game.hello = {**HELLO, "plugin_version": "0.8.1-r5"}
    game.on_job = lambda msg: (game.put(int(msg["args"].split(",")[1], 16), 0x40128A9B, 4), "done")[1]
    background, game.on_execute = game.on_execute, None
    live = LiveChannel(game.port, targets_file=targets, sleep=lambda s: None)
    try:
        live.give(1, 100)
        results = []
        for _ in range(TRANSIENT_TRIES):
            live.poll()                                                 # a client tick arms the watch again
            background(0x140000000 + 0x277e080)
            results.append(live.give(1, 100).state)
        assert results == ["not now"] * (TRANSIENT_TRIES - 1) + ["refused"] and live.refused == {1: 0x40128A9B}
    finally:
        live.close()
        game.close()


def test_live_channel_finds_an_unknown_game_version_by_signature_once(tmp_path):
    from crimsonipelago.game_adapter.runtime.live import LiveChannel
    from tests.unit.test_signatures import _image

    game, targets = _r4_game(tmp_path)
    game.hello = {**HELLO, "plugin_version": "0.8.1-r5", "pe_timestamp": 1800000000}   # a game update
    marks = {0x277E080: b"PROBE-function-start-0123456789", 0x2781780: b"GIVE-function-start-0123456789",
             0x2774B20: b"RUNNER-function-start-012345678"}
    reads_table = 0x2779000
    code = {rva: data for rva, data in marks.items()}
    code[reads_table] = b"ITEMS:" + bytes.fromhex("488b1d") + struct.pack("<i", 0x6D69AA8 - (reads_table + 13))
    image = _image({}, sections=((".text", 0x2770000, 0x20000, 0x60000020),))
    text = bytearray(b"\xcc" * 0x20000)
    for rva, data in code.items():
        text[rva - 0x2770000:rva - 0x2770000 + len(data)] = data
    game.overlay.update({0x140000000: image[0x140000000], 0x140000000 + 0x2770000: bytes(text)})
    doc = json.loads(targets.read_text())
    doc["builds"] = {}
    doc["signatures"] = {name: {"kind": "function", "pattern": data.hex(" ")} for name, data in
                         (("probe", marks[0x277E080]), ("give", marks[0x2781780]), ("runner", marks[0x2774B20]))}
    doc["signatures"]["item_table"] = {"kind": "global", "pattern": b"ITEMS:".hex(" ") + " 48 8b 1d ?? ?? ?? ??",
                                       "disp_at": 9, "insn_end": 13}
    doc["signature_defaults"] = {"reason": "0x0b", "knowledge_live": False}
    targets.write_text(json.dumps(doc))

    def the_game_commits(msg):
        game.put(int(msg["args"].split(",")[1], 16), 0, 4)
        return "done"

    game.on_job = the_game_commits
    events = []
    found_file = tmp_path / "found-targets.json"
    live = LiveChannel(game.port, on_event=events.append, targets_file=targets, sleep=lambda s: None,
                       found_file=found_file)
    try:
        assert live.give(1, 100).given
        assert int(game.job_msg["fn"], 16) == 0x140000000 + 0x2781780
        assert any("new to Crimsonipelago" in e for e in events) and any("Found them all" in e for e in events)
    finally:
        live.close()
    reads = len([m for m in game.received if m["cmd"] == "read"])
    live = LiveChannel(game.port, on_event=events.append, targets_file=targets, sleep=lambda s: None,
                       found_file=found_file)
    try:
        assert live.ready() and live.r3["item_table"] == 0x6D69AA8 and live.r3["found_by"] == "signatures"
        assert len([m for m in game.received if m["cmd"] == "read"]) == reads        # no second search
    finally:
        live.close()
        game.close()


def test_live_channel_stays_off_when_a_signature_is_missing(tmp_path):
    from crimsonipelago.game_adapter.runtime.live import LiveChannel
    from tests.unit.test_signatures import _image

    game, targets = _r4_game(tmp_path)
    game.hello = {**HELLO, "plugin_version": "0.8.1-r5", "pe_timestamp": 1800000000}
    image = _image({})
    game.overlay.update({0x140000000: image[0x140000000], 0x140000000 + 0x1000: image[0x140000000 + 0x1000]})
    doc = json.loads(targets.read_text())
    doc["builds"] = {}
    doc["signatures"] = {"give": {"kind": "function", "pattern": "de ad be ef ?? 01 02 03"}}
    targets.write_text(json.dumps(doc))
    live = LiveChannel(game.port, targets_file=targets, found_file=tmp_path / "found.json")
    try:
        assert not live.ready() and "give not found" in live.unsupported and "quit to desktop" in live.unsupported
        assert not (tmp_path / "found.json").exists()
    finally:
        live.close()
        game.close()


def test_find_functions_compares_what_it_finds_with_the_known_build(tmp_path):
    from crimsonipelago.game_adapter.runtime.signatures import find_functions
    from tests.unit.test_signatures import _image

    game, targets = _r4_game(tmp_path)
    game.hello = {**HELLO, "plugin_version": "0.8.1-r5"}
    give = b"GIVE-function-start-0123456789"
    image = _image({}, sections=((".text", 0x2780000, 0x4000, 0x60000020),))
    text = bytearray(b"\xcc" * 0x4000)
    text[0x1780:0x1780 + len(give)] = give
    game.overlay.update({0x140000000: image[0x140000000], 0x140000000 + 0x2780000: bytes(text)})
    doc = json.loads(targets.read_text())
    doc["signatures"] = {"give": {"kind": "function", "pattern": give.hex(" ")},
                         "runner": {"kind": "function", "pattern": "de ad be ef 00 11 22 33"}}
    targets.write_text(json.dumps(doc))
    said = []
    try:
        with ResearchBridge(port=game.port) as bridge:
            path = find_functions(bridge, tmp_path, say=said.append, targets_file=targets)
        report = json.loads(path.read_text())
    finally:
        game.close()
    rows = {r["name"]: r for r in report["targets"]}
    assert rows["give"]["verdict"] == "found, where expected" and rows["give"]["found"] == "0x2781780"
    assert rows["runner"]["verdict"] == "not found" and report["outcome"] == "not all found"
