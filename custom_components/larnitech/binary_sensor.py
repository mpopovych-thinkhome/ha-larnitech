# Updated: 2026-06-26 14:30
"""Larnitech discrete sensors (read-only), added/removed dynamically."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    ENTITY_ID_FORMAT,
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import callback

from .const import DOMAIN
from .entity import LarnitechEntity

# Larnitech type -> device_class. All read an on/off `status.state`.
BINARY_SENSORS = {
    "motion-sensor": BinarySensorDeviceClass.MOTION,
    "door-sensor": BinarySensorDeviceClass.DOOR,
    "leak-sensor": BinarySensorDeviceClass.MOISTURE,
}

_ON_VALUES = {"on", "open", "opened", "1", "true", "alarm", "detected", "leak"}
_OFF_VALUES = {"off", "closed", "close", "0", "false", "clear", "normal", "no", "idle", "ok"}


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    @callback
    def _add_new():
        current = {
            a for a, d in coordinator.data.items() if d.get("type") in BINARY_SENSORS
        }
        new = [
            LarnitechBinarySensor(
                coordinator, a, BINARY_SENSORS[coordinator.data[a]["type"]]
            )
            for a in current - known
        ]
        known.clear()
        known.update(current)
        if new:
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_listener(_add_new))
    _add_new()


class LarnitechBinarySensor(LarnitechEntity, BinarySensorEntity):
    def __init__(self, coordinator, addr, device_class):
        super().__init__(coordinator, addr)
        self._attr_device_class = device_class
        self.entity_id = ENTITY_ID_FORMAT.format(self._slug)

    @property
    def is_on(self) -> bool | None:
        value = self.status.get("state")
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        text = str(value).strip().lower()
        if text in _ON_VALUES:
            return True
        if text in _OFF_VALUES:
            return False
        self._warn_once(
            f"state:{value!r}",
            "Larnitech binary_sensor %s: unrecognized state %r (status=%s) — treating as off",
            self.entity_id,
            value,
            self.status,
        )
        return False
