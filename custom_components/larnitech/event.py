# Updated: 2026-08-20 13:50
"""Larnitech physical buttons (`switch`) as event entities.

A `switch` widget is a physical button input, NOT a relay. Status carries no
`state` key at all — it's a raw `{"hex": "0xBBCC"}` pair confirmed live
2026-08-20: byte0 (`BB`) is the key state (`0xFC` pressed, `0xFD` held,
`0xFF` released), byte1 (`CC`) a hold-duration counter in 128ms ticks.

Event names follow the Hue convention, HA's reference vocabulary for a
button that reports down / held / up.

**One WS push = one event.** Firing off the entity's own status is wrong:
HA runs every listener on every coordinator update (polls included, and any
other device's push), while `merge_status` never clears `hex` — so reading
the status unconditionally re-fired the last gesture hundreds of times an
hour. `coordinator.event_addrs` marks the addrs that actually arrived in
the push being delivered; anything else is a poll and must stay silent."""
from __future__ import annotations

from homeassistant.components.event import (
    ENTITY_ID_FORMAT,
    EventDeviceClass,
    EventEntity,
)
from homeassistant.core import callback

from .const import DOMAIN
from .entity import LarnitechEntity

TYPE = "switch"

EVENT_PRESS = "initial_press"
EVENT_REPEAT = "repeat"
EVENT_SHORT_RELEASE = "short_release"
EVENT_LONG_RELEASE = "long_release"

_PRESSED = 0xFC
_HELD = 0xFD
_RELEASED = 0xFF

_TICK_MS = 128


def _parse(raw) -> tuple[int | None, int]:
    """Split the `hex` status field into (key state, duration ms)."""
    if not isinstance(raw, str):
        return None, 0
    try:
        value = int(raw, 16)
    except ValueError:
        return None, 0
    return (value >> 8) & 0xFF, (value & 0xFF) * _TICK_MS


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    @callback
    def _add_new():
        current = {a for a, d in coordinator.data.items() if d.get("type") == TYPE}
        new = [LarnitechButton(coordinator, a) for a in current - known]
        known.clear()
        known.update(current)
        if new:
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_listener(_add_new))
    _add_new()


class LarnitechButton(LarnitechEntity, EventEntity):
    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = [
        EVENT_PRESS,
        EVENT_REPEAT,
        EVENT_SHORT_RELEASE,
        EVENT_LONG_RELEASE,
    ]
    _attr_icon = "mdi:light-switch"

    def __init__(self, coordinator, addr):
        super().__init__(coordinator, addr)
        self.entity_id = ENTITY_ID_FORMAT.format(self._oid())

    @callback
    def _handle_coordinator_update(self) -> None:
        if self._addr in self.coordinator.event_addrs:
            key_state, duration_ms = _parse(self.status.get("hex"))
            if key_state == _PRESSED:
                self._trigger_event(EVENT_PRESS)
            elif key_state == _HELD:
                self._trigger_event(EVENT_REPEAT, {"duration_ms": duration_ms})
            elif key_state == _RELEASED:
                # The counter is the whole distinction: a tap releases with it
                # still at zero, anything held long enough to tick is a long
                # press ending.
                event = EVENT_LONG_RELEASE if duration_ms else EVENT_SHORT_RELEASE
                self._trigger_event(event, {"duration_ms": duration_ms})

        super()._handle_coordinator_update()
