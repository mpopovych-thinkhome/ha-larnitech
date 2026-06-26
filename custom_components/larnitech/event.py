# Updated: 2026-06-26 14:30
"""Larnitech physical buttons (`switch`) as event entities (press / hold).

A `switch` widget is a physical button input, NOT a relay. The decoded status
carries the gesture; pushes arrive as status changes, so each gesture fires an
HA event. The byte->gesture decode is provisional until confirmed against a
live press on the stand (watch the logbook while pressing)."""
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

EVENT_PRESS = "press"
EVENT_HOLD = "hold"

# Raw status.state values treated as the resting (no-gesture) state.
_IDLE_VALUES = {None, "", "off", "0", "0x00", "idle", "released", "release", 0, False}
# Raw values that mean a long press; anything else non-idle is a short press.
_HOLD_VALUES = {"hold", "long", "long-press", "0x02"}


def _decode_event(raw) -> str | None:
    """Map a raw `status.state` to an event_type, or None when idle.

    PROVISIONAL: exact decoded values are unconfirmed. Confirm on the stand
    and adjust _IDLE_VALUES / _HOLD_VALUES accordingly."""
    if raw in _IDLE_VALUES:
        return None
    if isinstance(raw, str) and raw.strip().lower() in _HOLD_VALUES:
        return EVENT_HOLD
    return EVENT_PRESS


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
    _attr_event_types = [EVENT_PRESS, EVENT_HOLD]

    def __init__(self, coordinator, addr):
        super().__init__(coordinator, addr)
        self.entity_id = ENTITY_ID_FORMAT.format(self._slug)
        # Seed with the current value so a stale status doesn't fire on startup.
        self._last = self.status.get("state")

    @callback
    def _handle_coordinator_update(self) -> None:
        raw = self.status.get("state")
        if raw != self._last:
            self._last = raw
            event_type = _decode_event(raw)
            if event_type:
                self._trigger_event(event_type)
        super()._handle_coordinator_update()
