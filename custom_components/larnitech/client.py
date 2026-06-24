# Updated: 2026-06-24 15:15
"""Larnitech API2 WebSocket client: persistent connection, push + request/response."""
from __future__ import annotations

import asyncio
import json
import logging

import websockets

from .const import CONN_CLOUD, DEFAULT_LOCAL_PORT

_LOGGER = logging.getLogger(__name__)

# Controller quirk: "json"-type widgets emit `"status":{{...}` with a doubled
# opening brace (invalid JSON: a key is expected after `{`). Braces stay
# balanced, so we insert a placeholder key instead of dropping a brace.
_QUIRK_FROM = '"status":{{'
_QUIRK_TO = '"status":{"_raw":{'


class LarnitechError(Exception):
    """Base error."""


class LarnitechAuthError(LarnitechError):
    """Authorization rejected by the controller."""


class LarnitechClient:
    """Holds one persistent WebSocket. A supervisor task keeps it connected and
    subscribed; the same socket serves request/response (get-devices, status-set)."""

    def __init__(
        self, connection_type, key, serial=None, host=None,
        port=DEFAULT_LOCAL_PORT, ssl_context=None,
    ):
        self._connection_type = connection_type
        self._key = key
        self.serial = serial
        self._host = host
        self._port = port
        self._ssl_context = ssl_context

        self._ws = None
        self._supervisor = None
        self._closing = False
        self._connected = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._poll_lock = asyncio.Lock()
        self._devices_future: asyncio.Future | None = None
        self._on_event = None
        self._on_resync = None

    @property
    def url(self) -> str:
        if self._connection_type == CONN_CLOUD:
            return f"wss://{self.serial}.in.larnitech.com:8443/api"
        return f"ws://{self._host}:{self._port}/api"

    def set_event_callback(self, callback) -> None:
        """Sync callback(devices: list) fired on each decoded state-change event."""
        self._on_event = callback

    def set_resync_callback(self, callback) -> None:
        """Sync callback() fired on every (re)connect to pull a full snapshot."""
        self._on_resync = callback

    # --- lifecycle -------------------------------------------------------

    async def async_start(self) -> None:
        """Start the supervisor and wait for the first connect + subscribe."""
        self._closing = False
        self._supervisor = asyncio.create_task(self._run())
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=20)
        except asyncio.TimeoutError as err:
            raise LarnitechError("initial connection timed out") from err

    async def async_close(self) -> None:
        self._closing = True
        # Cancel the supervisor first: CancelledError breaks the `async for`
        # immediately; its finally then closes the socket quickly.
        if self._supervisor is not None:
            self._supervisor.cancel()
            try:
                await self._supervisor
            except asyncio.CancelledError:
                pass
            self._supervisor = None
        await self._safe_close()

    async def _run(self) -> None:
        backoff = 1
        while not self._closing:
            try:
                await self._open()
                # status=detailed makes events arrive decoded (JSON), not hex.
                await self._send({"request": "status-subscribe", "status": "detailed"})
                self._connected.set()
                backoff = 1
                if self._on_resync is not None:
                    self._on_resync()  # pull a full fresh snapshot on (re)connect
                async for raw in self._ws:
                    self._handle(raw)
            except (OSError, websockets.WebSocketException, asyncio.TimeoutError) as err:
                _LOGGER.debug("Larnitech connection lost: %s", err)
            except Exception:  # noqa: BLE001 - supervisor must never die silently
                _LOGGER.exception("Larnitech listener crashed")
            finally:
                self._connected.clear()
                await self._safe_close()
            if not self._closing:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _open(self) -> None:
        # The Larnitech cloud does not answer WebSocket pings, so client-side
        # keepalive pings would drop the link on a false "ping timeout". Disable
        # them; the periodic get-devices poll keeps the socket alive and detects
        # a dead connection.
        # close_timeout small: the cloud never sends a close frame, so a normal
        # close would otherwise hang waiting for one.
        kwargs = {"open_timeout": 15, "ping_interval": None, "close_timeout": 1}
        if self.url.startswith("wss"):
            kwargs["ssl"] = self._ssl_context
        self._ws = await websockets.connect(self.url, **kwargs)
        await self._ws.send(json.dumps({"request": "authorize", "key": self._key}))
        resp = json.loads(await self._ws.recv())
        if resp.get("result") != "success":
            raise LarnitechAuthError(f"authorize rejected: {resp}")

    # --- message handling ------------------------------------------------

    def _handle(self, raw: str) -> None:
        raw = raw.replace(_QUIRK_FROM, _QUIRK_TO)
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            _LOGGER.debug("Larnitech: undecodable frame")
            return
        if msg.get("response") == "get-devices":
            fut = self._devices_future
            if fut is not None and not fut.done():
                fut.set_result(msg.get("devices", []))
        elif msg.get("event") == "statuses" and self._on_event is not None:
            self._on_event(msg.get("devices", []))

    # --- requests --------------------------------------------------------

    async def async_get_devices(self) -> list[dict]:
        """Request the full decoded snapshot; the listener resolves the future."""
        async with self._poll_lock:
            if self._ws is None:
                raise LarnitechError("not connected")
            loop = asyncio.get_running_loop()
            self._devices_future = loop.create_future()
            try:
                await self._send({"request": "get-devices", "status": "detailed"})
                return await asyncio.wait_for(self._devices_future, timeout=15)
            except (OSError, websockets.WebSocketException, asyncio.TimeoutError) as err:
                raise LarnitechError(f"get-devices failed: {err}") from err
            finally:
                self._devices_future = None

    async def async_set_status(self, addr: str, status: dict) -> None:
        if self._ws is None:
            raise LarnitechError("not connected")
        try:
            await self._send({"request": "status-set", "addr": addr, "status": status})
        except (OSError, websockets.WebSocketException) as err:
            raise LarnitechError(f"status-set failed: {err}") from err

    async def async_test(self) -> None:
        """One-shot connect + auth for the config flow (no listener)."""
        try:
            await self._open()
        except LarnitechAuthError:
            raise
        except (OSError, websockets.WebSocketException, asyncio.TimeoutError) as err:
            raise LarnitechError(f"connection failed: {err}") from err
        finally:
            await self._safe_close()

    async def _send(self, obj: dict) -> None:
        async with self._send_lock:
            if self._ws is None:
                raise LarnitechError("not connected")
            await self._ws.send(json.dumps(obj))

    async def _safe_close(self) -> None:
        if self._ws is not None:
            ws, self._ws = self._ws, None
            try:
                await asyncio.wait_for(ws.close(), timeout=2)
            except BaseException:  # noqa: BLE001 - closing must never block or raise
                pass
