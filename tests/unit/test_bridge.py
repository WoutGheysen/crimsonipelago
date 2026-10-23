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
                        if msg.get("cmd") == "ping":
                            reply = {"type": "pong", "id": msg["id"], "uptime_ms": 99}
                        elif msg.get("cmd") == "info":
                            reply = {**self.hello, "type": "info"}
                        else:
                            reply = {"type": "error", "message": "unknown command"}
                        conn.sendall((json.dumps(reply) + "\n").encode())

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


class FakeMemory(FakePlugin):
    """A stand-in plugin with a small memory: copper sits 16 bytes after item key 1, plus decoys."""

    def __init__(self):
        self.memory = {0x1000 + 8 * i: 800 for i in range(10)}          # decoys holding the same value
        self.copper = 0x9000
        self.memory[self.copper] = 800
        self.hits = []
        super().__init__()

    def _bytes(self, start, size):
        out = bytearray(size)
        for addr, value in self.memory.items():
            if start <= addr < start + size - 7:
                struct.pack_into("<Q", out, addr - start, value)
        if start <= self.copper - 16 < start + size - 3:
            struct.pack_into("<I", out, self.copper - 16 - start, 1)
        return bytes(out)

    def _serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn:
                conn.sendall((json.dumps(HELLO) + "\n").encode())
                buf = b""
                while True:
                    data = conn.recv(4096)
                    if not data:
                        break
                    buf += data
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        msg = json.loads(line)
                        cmd = msg["cmd"]
                        if cmd in ("scan_u64", "rescan_u64"):
                            pool = self.memory if cmd == "scan_u64" else self.hits
                            self.hits = sorted(a for a in pool if self.memory[a] == msg["value"])
                            reply = {"type": "scan", "count": len(self.hits), "truncated": False, "ms": 1,
                                     "hits": [hex(a) for a in self.hits]}
                        elif cmd == "read":
                            reply = {"type": "bytes", "hex": self._bytes(int(msg["addr"], 16), msg["size"]).hex()}
                        elif cmd == "write_u64":
                            addr = int(msg["addr"], 16)
                            ok = self.memory.get(addr) == msg["expect"]
                            found = self.memory.get(addr, 0)
                            if ok:
                                self.memory[addr] = msg["value"]
                            reply = {"type": "written", "ok": ok, "found": found}
                        conn.sendall((json.dumps(reply) + "\n").encode())

    def spend(self, amount):
        self.memory[self.copper] -= amount


def test_find_copper_narrows_down_and_writes_live():
    game = FakeMemory()
    try:
        answers = iter(["8.00", "7.50", "y"])
        said = []

        def ask(prompt):
            said.append(prompt)
            answer = next(answers)
            if answer == "7.50":
                game.spend(50)          # the player buys something between the two searches
            return answer

        with ResearchBridge(port=game.port) as bridge:
            assert research.find_copper(bridge, ask=ask, say=said.append)
        assert game.memory[game.copper] == 850
        assert all(v == 800 for a, v in game.memory.items() if a != game.copper)   # decoys untouched
        assert any("item key 1 at -16 bytes" in line for line in said)
    finally:
        game.close()


def test_money_is_read_as_shown():
    assert research.parse_money("8.00") == 800
    assert research.parse_money("8,5") == 850
    assert research.parse_money("123") == 123
    assert research.parse_money("abc") is None
