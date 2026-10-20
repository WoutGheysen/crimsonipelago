"""WebSocket transport to an Archipelago server, with automatic reconnect."""

from __future__ import annotations

import asyncio
import logging
import ssl
from typing import Awaitable, Callable, Iterable

import websockets
from websockets.exceptions import ConnectionClosed, InvalidURI, InvalidHandshake

from . import protocol

log = logging.getLogger("crimsonipelago.connection")

PacketHandler = Callable[[dict], Awaitable[None]]
StateHandler = Callable[[str, str], Awaitable[None]]  # (state, detail)

DEFAULT_PORT = 38281
BACKOFF = (1, 2, 5, 10, 20, 30)


def candidate_urls(address: str, port: int | None = None) -> list[str]:
    """Expand a user-typed address into URLs to try, in order.

    "archipelago.gg:38281"  -> wss://..., then ws://...
    "ws://localhost:38281"  -> as given
    """
    address = address.strip()
    if address.startswith(("ws://", "wss://")):
        return [address]
    host = address
    if ":" not in address.rsplit("]", 1)[-1]:
        host = f"{address}:{port or DEFAULT_PORT}"
    elif port:
        host = f"{address.rsplit(':', 1)[0]}:{port}"
    return [f"wss://{host}", f"ws://{host}"]


class APConnection:
    """Owns the socket. Everything protocol-level is handled by the caller's packet handler."""

    def __init__(self, address: str, port: int | None, on_packet: PacketHandler, on_state: StateHandler):
        self.urls = candidate_urls(address, port)
        self.on_packet = on_packet
        self.on_state = on_state
        self._ws = None
        self._stop = asyncio.Event()
        self._working_url: str | None = None
        self.fatal_error: str | None = None

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def send(self, commands: Iterable[dict]) -> bool:
        ws = self._ws
        if ws is None:
            return False
        try:
            await ws.send(protocol.encode(commands))
            return True
        except ConnectionClosed:
            return False

    def stop(self) -> None:
        self._stop.set()
        self._close_socket()

    def fail(self, message: str) -> None:
        """Stop reconnecting because of a problem retrying will not fix."""
        self.fatal_error = message
        self.stop()

    def _close_socket(self) -> None:
        ws = self._ws
        if ws is not None:
            try:
                asyncio.get_running_loop().create_task(ws.close())
            except RuntimeError:  # no running loop (called during interpreter shutdown)
                pass

    async def _open(self):
        urls = [self._working_url] if self._working_url else self.urls
        last_exc: Exception | None = None
        for url in urls:
            try:
                kwargs = {"max_size": None, "ping_interval": 20, "ping_timeout": 30, "open_timeout": 10}
                if url.startswith("wss://"):
                    kwargs["ssl"] = ssl.create_default_context()
                ws = await websockets.connect(url, **kwargs)
                self._working_url = url
                return ws
            except (OSError, InvalidHandshake, InvalidURI, asyncio.TimeoutError, ssl.SSLError) as exc:
                last_exc = exc
                log.debug("connect to %s failed: %s", url, exc)
        raise ConnectionError(str(last_exc) if last_exc else "no URL to connect to")

    async def run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            await self.on_state("connecting", self._working_url or self.urls[0])
            try:
                ws = await self._open()
            except ConnectionError as exc:
                delay = BACKOFF[min(attempt, len(BACKOFF) - 1)]
                attempt += 1
                await self.on_state("disconnected", f"Archipelago server unavailable ({exc}); retrying in {delay}s")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
                continue

            attempt = 0
            self._ws = ws
            await self.on_state("socket_open", self._working_url or "")
            try:
                async for message in ws:
                    try:
                        commands = protocol.decode(message)
                    except ValueError as exc:
                        log.warning("ignoring malformed packet: %s", exc)
                        continue
                    for cmd in commands:
                        await self.on_packet(cmd)
            except ConnectionClosed as exc:
                log.info("connection closed: %s", exc)
            finally:
                self._ws = None
            if self._stop.is_set():
                break
            await self.on_state("disconnected", "connection to the Archipelago server was lost; reconnecting")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=1)
            except asyncio.TimeoutError:
                pass
        await self.on_state("stopped", self.fatal_error or "")
