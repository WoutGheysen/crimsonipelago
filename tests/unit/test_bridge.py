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
            return {"type": "bytes", "hex": self.mem[start - BASE:start - BASE + msg["size"]].hex()}
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
            return {"type": "watching", "slot": len(self.watches) - 1, "threads": 12}
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
    answers = iter(["8.00", "7.00", "", "", "d"])

    def ask(prompt):
        said.append(prompt)
        answer = next(answers)
        if "amount" in prompt and answer:
            for addr in (record, total):
                game.put(addr, research.parse_money(answer), 4)
        if "Enter after each one" in prompt:
            next(actions)()
        return answer

    try:
        with ResearchBridge(port=game.port) as bridge:
            path = research.watch_money(bridge, tmp_path, ask=ask, say=said.append, wait=lambda s: None)
        assert path is not None and path.exists()
        report = json.loads(path.read_text())
        assert len(report["records"]) == 72 and report["sites"] == {"CrimsonDesert.exe+0x123": 72}
        assert [w["addr"] for w in report["watched"]] == [hex(record), hex(total)]
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
