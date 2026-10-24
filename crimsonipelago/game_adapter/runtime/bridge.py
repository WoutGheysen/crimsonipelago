"""The client's side of the link to the in-game plugin (see plugin/src/main.cpp for the protocol)."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from typing import Any

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 38301
PROTOCOL = 1


class BridgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class PluginInfo:
    plugin_version: str
    protocol: int
    pid: int
    exe: str
    exe_version: str
    module_base: str
    module_size: int
    pe_timestamp: int
    uptime_ms: int

    @classmethod
    def from_message(cls, msg: dict[str, Any]) -> PluginInfo:
        try:
            return cls(str(msg["plugin_version"]), int(msg["protocol"]), int(msg["pid"]), str(msg["exe"]),
                       str(msg["exe_version"]), str(msg["module_base"]), int(msg["module_size"]),
                       int(msg["pe_timestamp"]), int(msg["uptime_ms"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise BridgeError(f"unexpected message from the plugin: {msg}") from exc

    def describe(self) -> str:
        return (f"plugin {self.plugin_version}, game {self.exe_version} (build stamp {self.pe_timestamp}), "
                f"pid {self.pid}")


class GameBridge:
    """A blocking, line-based connection to the plugin. Small on purpose: one request, one reply."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 5.0):
        self.host, self.port, self.timeout = host, port, timeout
        self._sock: socket.socket | None = None
        self._buffer = b""
        self._next_id = 1
        self.info: PluginInfo | None = None

    # ------------------------------------------------------------------ connection
    def connect(self) -> PluginInfo:
        try:
            self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as exc:
            raise BridgeError(f"no game plugin on {self.host}:{self.port} ({exc.strerror or exc})") from exc
        hello = self._read()
        if hello.get("type") != "hello" or hello.get("plugin") != "crimsonipelago":
            self.close()
            raise BridgeError(f"something else is listening on port {self.port}: {hello}")
        if int(hello.get("protocol", 0)) != PROTOCOL:
            self.close()
            raise BridgeError(f"plugin speaks protocol {hello.get('protocol')}, this client {PROTOCOL}: update one of them")
        self.info = PluginInfo.from_message(hello)
        return self.info

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def __enter__(self) -> GameBridge:
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ requests
    def ping(self) -> int:
        """Round trip; returns the plugin's uptime in milliseconds."""
        request_id = self._next_id
        self._next_id += 1
        reply = self.request({"cmd": "ping", "id": request_id})
        if reply.get("type") != "pong" or int(reply.get("id", -1)) != request_id:
            raise BridgeError(f"unexpected reply to ping: {reply}")
        return int(reply.get("uptime_ms", 0))

    def refresh_info(self) -> PluginInfo:
        reply = self.request({"cmd": "info"})
        if reply.get("type") != "info":
            raise BridgeError(f"unexpected reply to info: {reply}")
        self.info = PluginInfo.from_message(reply)
        return self.info

    def request(self, message: dict[str, Any]) -> dict[str, Any]:
        if self._sock is None:
            raise BridgeError("not connected to the game plugin")
        try:
            self._sock.sendall((json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8"))
        except OSError as exc:
            self.close()
            raise BridgeError(f"lost the game plugin ({exc})") from exc
        return self._read()

    def _read(self) -> dict[str, Any]:
        assert self._sock is not None
        while b"\n" not in self._buffer:
            try:
                chunk = self._sock.recv(4096)
            except OSError as exc:
                self.close()
                raise BridgeError(f"lost the game plugin ({exc})") from exc
            if not chunk:
                self.close()
                raise BridgeError("the game plugin closed the connection")
            self._buffer += chunk
            if len(self._buffer) > 16 << 20:
                self.close()
                raise BridgeError("oversized message from the game plugin")
        line, self._buffer = self._buffer.split(b"\n", 1)
        try:
            msg = json.loads(line.decode("utf-8"))
        except ValueError as exc:
            raise BridgeError(f"unreadable message from the game plugin: {line[:200]!r}") from exc
        if not isinstance(msg, dict):
            raise BridgeError(f"unexpected message from the game plugin: {msg!r}")
        return msg


# --------------------------------------------------------------------------- research (R1)
@dataclass(frozen=True)
class ScanReport:
    count: int
    hits: list[int]           # the first 64 addresses
    truncated: bool
    ms: int


def _scan_report(reply: dict[str, Any]) -> ScanReport:
    if reply.get("type") != "scan":
        raise BridgeError(reply.get("message") or f"unexpected reply: {reply}")
    return ScanReport(int(reply["count"]), [int(h, 16) for h in reply.get("hits", [])], bool(reply.get("truncated")),
                      int(reply.get("ms", 0)))


@dataclass(frozen=True)
class WatchHits:
    total: int                         # records made so far
    lost: int                          # records overwritten before they were collected
    records: list[dict[str, Any]]      # slot, tid, value, rip, fn, code, frames, stack
    sites: dict[str, int]              # every writing instruction seen, with how often it wrote


class ResearchBridge(GameBridge):
    """Memory search commands of the R1 plugin. Searches can take seconds, so the timeout is longer."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("timeout", 120.0)
        super().__init__(*args, **kwargs)

    def scan(self, lo: int, hi: int | None = None, *, width: int = 8, align: int = 4) -> ScanReport:
        """New search: every `align`-aligned `width`-byte value in [lo, hi] (hi defaults to lo)."""
        return _scan_report(self.request({"cmd": "scan", "lo": int(lo), "hi": int(lo if hi is None else hi),
                                          "width": int(width), "align": int(align)}))

    def rescan(self, lo: int, hi: int | None = None) -> ScanReport:
        """Keep the previous search's hits whose value is now in [lo, hi]; same width as that search."""
        return _scan_report(self.request({"cmd": "rescan", "lo": int(lo), "hi": int(lo if hi is None else hi)}))

    def scan_u64(self, value: int) -> ScanReport:
        return _scan_report(self.request({"cmd": "scan_u64", "value": int(value)}))

    def rescan_u64(self, value: int) -> ScanReport:
        return _scan_report(self.request({"cmd": "rescan_u64", "value": int(value)}))

    def read(self, addr: int, size: int) -> bytes:
        reply = self.request({"cmd": "read", "addr": hex(addr), "size": int(size)})
        if reply.get("type") != "bytes":
            raise BridgeError(reply.get("message") or f"unexpected reply: {reply}")
        return bytes.fromhex(reply["hex"])

    def read_int(self, addr: int, width: int = 8) -> int:
        return int.from_bytes(self.read(addr, width), "little")

    def write(self, addr: int, expect: int, value: int, *, width: int = 8) -> tuple[bool, int]:
        """Compare-and-set: writes only if the address still holds `expect`. Returns (written, value found)."""
        reply = self.request({"cmd": "write", "addr": hex(addr), "expect": int(expect), "value": int(value),
                              "width": int(width)})
        if reply.get("type") != "written":
            raise BridgeError(reply.get("message") or f"unexpected reply: {reply}")
        return bool(reply["ok"]), int(reply["found"])

    def write_u64(self, addr: int, expect: int, value: int) -> tuple[bool, int]:
        """The 8-byte form of write() (R1's first version)."""
        reply = self.request({"cmd": "write_u64", "addr": hex(addr), "expect": int(expect), "value": int(value)})
        if reply.get("type") != "written":
            raise BridgeError(reply.get("message") or f"unexpected reply: {reply}")
        return bool(reply["ok"]), int(reply["found"])

    # ------------------------------------------------------------------ write watches (R2)
    def watch(self, addr: int, size: int = 8, mode: str = "write") -> tuple[int, int]:
        """Watch writes to `addr` (mode "access": reads too; "execute": calls of the function starting there,
        with their arguments). Returns (slot, threads armed)."""
        reply = self.request({"cmd": "watch", "addr": hex(addr), "size": int(size), "mode": mode})
        if reply.get("type") != "watching":
            raise BridgeError(reply.get("message") or f"unexpected reply: {reply}")
        return int(reply["slot"]), int(reply["threads"])

    def watch_apply(self) -> int:
        """Arm the watches on threads the game started since. Returns the threads armed."""
        reply = self.request({"cmd": "watch_apply"})
        if reply.get("type") != "watching":
            raise BridgeError(reply.get("message") or f"unexpected reply: {reply}")
        return int(reply["threads"])

    def unwatch(self) -> int:
        reply = self.request({"cmd": "unwatch"})
        if reply.get("type") != "unwatched":
            raise BridgeError(reply.get("message") or f"unexpected reply: {reply}")
        return int(reply["threads"])

    def function_bounds(self, addr: int) -> tuple[int, int]:
        """Start and end of the function holding `addr`, from the unwind data; (0, 0) when it has none."""
        reply = self.request({"cmd": "function", "addr": hex(addr)})
        if reply.get("type") != "function":
            raise BridgeError(reply.get("message") or f"unexpected reply: {reply}")
        return int(reply["begin"], 16), int(reply["end"], 16)

    def hits(self, max_records: int = 64) -> WatchHits:
        """Records since the last call (up to 8 per writing instruction), and every writing instruction seen."""
        reply = self.request({"cmd": "hits", "max": int(max_records)})
        if reply.get("type") != "hits":
            raise BridgeError(reply.get("message") or f"unexpected reply: {reply}")
        sites = {str(s["rip"]): int(s["count"]) for s in reply.get("sites", [])}
        return WatchHits(int(reply["total"]), int(reply["lost"]), list(reply["records"]), sites)
