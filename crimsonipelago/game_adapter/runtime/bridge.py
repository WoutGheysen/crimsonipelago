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

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 2.0):
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
            if len(self._buffer) > 1 << 20:
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
