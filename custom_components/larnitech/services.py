"""`larnitech.modbus_request` — a Modbus RTU/TCP exchange on a `com-port`.

Framing lives in `modbus.py`; this is the transport: send the frame as the
port's string status, take the answer from the port's push events.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .client import LarnitechError
from .const import DOMAIN
from .modbus import (
    READ_FUNCTIONS,
    WRITE_FUNCTIONS,
    ModbusExceptionResponse,
    build_request,
    parse_answer,
)

SERVICE_MODBUS_REQUEST = "modbus_request"

# Answers were measured at ~0.2s, but they queue behind the script master's own
# polling on the same bus.
_ANSWER_TIMEOUT = 3
# See `_exchange`: how many times to try moving the port off a frame that
# already looks like our answer before sending anyway.
_SEPARATE_ATTEMPTS = 3

_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): cv.string,
        vol.Required("port"): cv.string,
        vol.Required("slave"): vol.All(vol.Coerce(int), vol.Range(min=1, max=247)),
        vol.Required("function"): vol.All(vol.Coerce(int), vol.In(READ_FUNCTIONS + WRITE_FUNCTIONS)),
        vol.Required("address"): vol.All(vol.Coerce(int), vol.Range(min=0, max=0xFFFF)),
        vol.Optional("count", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=125)),
        vol.Optional("values"): vol.All(
            cv.ensure_list,
            [vol.All(vol.Coerce(int), vol.Range(min=0, max=0xFFFF))],
            vol.Length(min=1, max=123),
        ),
    }
)

# One exchange at a time per controller: answers are told apart only by
# slave/function/size, so two of ours in flight could take each other's.
_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    hass.services.async_register(
        DOMAIN,
        SERVICE_MODBUS_REQUEST,
        _async_modbus_request,
        schema=_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


async def _async_modbus_request(call: ServiceCall) -> dict:
    entry_id, port = call.data["config_entry_id"], call.data["port"]
    coordinator = call.hass.data.get(DOMAIN, {}).get(entry_id)
    if coordinator is None or (coordinator.data or {}).get(port, {}).get("type") != "com-port":
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="modbus_bad_port",
            translation_placeholders={"port": port},
        )
    function, values = call.data["function"], call.data.get("values")
    if function in WRITE_FUNCTIONS:
        if coordinator.read_only:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="read_only_write_blocked",
                translation_placeholders={"title": coordinator.entry.title},
            )
        if not values or (function == 6 and len(values) != 1):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="modbus_bad_values"
            )

    request = build_request(
        call.data["slave"], function, call.data["address"], call.data["count"], values
    )
    try:
        async with _locks[entry_id]:
            registers = await _exchange(coordinator, port, request)
    except TimeoutError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="modbus_timeout",
            translation_placeholders={"port": port},
        ) from err
    except ModbusExceptionResponse as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="modbus_exception",
            translation_placeholders={"code": str(err.code)},
        ) from err
    except LarnitechError as err:
        raise HomeAssistantError(str(err)) from err
    return {"registers": registers} if function in READ_FUNCTIONS else {}


def _last_frame(coordinator, port: str) -> bytes | None:
    raw = coordinator.data.get(port, {}).get("status", {}).get("hex")
    if not isinstance(raw, str) or raw[:2].lower() != "0x":
        return None
    try:
        return bytes.fromhex(raw[2:])
    except ValueError:
        return None


def _looks_like_answer(request: bytes, frame: bytes | None) -> bool:
    if frame is None:
        return False
    try:
        return parse_answer(request, frame) is not None
    except ModbusExceptionResponse:
        return True


def _separator(request: bytes) -> bytes:
    """A harmless read whose answer can never equal `request`'s: a read of the
    same register with a different count (different size), or a one-register
    read for a write (different function)."""
    slave, function, address = request[0], request[1], int.from_bytes(request[2:4])
    if function in READ_FUNCTIONS:
        count = int.from_bytes(request[4:6])
        return build_request(slave, function, address, count + 1 if count < 125 else count - 1)
    return build_request(slave, 3, address, 1)


async def _exchange(coordinator, port: str, request: bytes) -> list[int]:
    """The controller reports only a CHANGE of the port's last received frame,
    so an answer identical to the frame already there produces no event at all
    — a repeated read of an unchanged register, or the same write twice. Taking
    the stored frame instead would pass off a device gone silent as a fresh
    answer. So when the port already holds something that reads as our answer,
    first move it to a different frame with a separator read; then our own
    answer, if one comes, always arrives as an event."""
    separator = _separator(request)
    for _ in range(_SEPARATE_ATTEMPTS):
        if not _looks_like_answer(request, _last_frame(coordinator, port)):
            break
        # A silent device or an exception answer still serves: either the frame
        # moved, or the request below times out honestly.
        with contextlib.suppress(TimeoutError, ModbusExceptionResponse):
            await _send_and_wait(coordinator, port, separator)
    return await _send_and_wait(coordinator, port, request)


async def _send_and_wait(coordinator, port: str, request: bytes) -> list[int]:
    answered: asyncio.Future = asyncio.get_running_loop().create_future()

    @callback
    def _on_update() -> None:
        # `event_addrs` is set only while a push is being delivered.
        if answered.done() or port not in coordinator.event_addrs:
            return
        frame = _last_frame(coordinator, port)
        if frame is None:
            return
        try:
            result = parse_answer(request, frame)
        except ModbusExceptionResponse as err:
            answered.set_exception(err)
            return
        if result is not None:
            answered.set_result(result)

    remove = coordinator.async_add_listener(_on_update)
    try:
        await coordinator.client.async_set_status(port, "0x" + request.hex().upper())
        return await asyncio.wait_for(answered, _ANSWER_TIMEOUT)
    finally:
        remove()
