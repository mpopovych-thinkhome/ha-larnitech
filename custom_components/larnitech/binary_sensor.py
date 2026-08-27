# Updated: 2026-08-27 15:39
"""Larnitech discrete sensors (read-only), added/removed dynamically."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    ENTITY_ID_FORMAT,
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import callback

from .const import DOMAIN
from .entity import LarnitechEntity

# Larnitech type -> device_class. All read an on/off `status.state`.
BINARY_SENSORS = {
    "motion-sensor": BinarySensorDeviceClass.MOTION,
    "door-sensor": BinarySensorDeviceClass.DOOR,
    "leak-sensor": BinarySensorDeviceClass.MOISTURE,
}

# `door-sensor` sub-type -> device_class. `door-sensor` is Larnitech's
# generic contact-input widget — sub-type picks the real semantics (fire
# alarm relay, gas/CO2 threshold contact, glass-break, lock state, ...), not
# just an icon, so it must not surface as a plain "door" for these. Absent
# or unrecognized sub-type falls back to DOOR (see BINARY_SENSORS above).
DOOR_SENSOR_SUBTYPE = {
    "contact": BinarySensorDeviceClass.OPENING,
    "motion": BinarySensorDeviceClass.MOTION,
    "fire": BinarySensorDeviceClass.HEAT,
    "smoke": BinarySensorDeviceClass.SMOKE,
    "gas": BinarySensorDeviceClass.GAS,
    "co2": BinarySensorDeviceClass.PROBLEM,
    "leak": BinarySensorDeviceClass.MOISTURE,
    "glass": BinarySensorDeviceClass.TAMPER,
    "lock": BinarySensorDeviceClass.LOCK,
    "alarm": BinarySensorDeviceClass.SAFETY,
}


def _device_class(device: dict):
    dtype = device.get("type")
    if dtype == "door-sensor":
        return DOOR_SENSOR_SUBTYPE.get(device.get("sub-type"), BinarySensorDeviceClass.DOOR)
    return BINARY_SENSORS.get(dtype)


# Types that can report `status.malfunction` (a fault code) ALONGSIDE their
# normal state — confirmed live 2026-08-20 on a real leak-sensor (wiring
# fault). A companion diagnostic entity surfaces this as its own signal,
# independent of the primary entity's domain (binary leak-sensor vs. light
# rgb-lamp both ride here). `rgb-lamp` here is the real type only — the
# virtual rgb-lamp this was first seen on (1:224) was cancelled 2026-08-21
# (bogus 101.6 level/saturation/hue, past the valid 0-100 range).
DIAGNOSTIC_MALFUNCTION = {"leak-sensor", "rgb-lamp"}

_ON_VALUES = {"on", "open", "opened", "1", "true", "alarm", "detected", "leak"}
_OFF_VALUES = {"off", "closed", "close", "0", "false", "clear", "normal", "no", "idle", "ok"}


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[tuple[str, str]] = set()

    @callback
    def _add_new():
        current = {(a, "main") for a, d in coordinator.data.items() if _device_class(d)}
        current |= {
            (a, "malfunction")
            for a, d in coordinator.data.items()
            if d.get("type") in DIAGNOSTIC_MALFUNCTION
        }
        new = []
        for addr, kind in current - known:
            if kind == "main":
                new.append(
                    LarnitechBinarySensor(coordinator, addr, _device_class(coordinator.data[addr]))
                )
            else:
                new.append(LarnitechMalfunctionSensor(coordinator, addr))
        known.clear()
        known.update(current)
        if new:
            async_add_entities(new)

    entry.async_on_unload(coordinator.add_discovery_listener(_add_new))
    _add_new()


class LarnitechBinarySensor(LarnitechEntity, BinarySensorEntity):
    def __init__(self, coordinator, addr, device_class):
        super().__init__(coordinator, addr)
        self._attr_device_class = device_class
        self.entity_id = ENTITY_ID_FORMAT.format(self._oid())

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


class LarnitechMalfunctionSensor(LarnitechEntity, BinarySensorEntity):
    """Companion diagnostic sensor: on when the device reports a fault
    (`status.malfunction`) instead of its normal `state`."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, addr):
        super().__init__(coordinator, addr)
        self._attr_unique_id = f"{self._slug}_malfunction"
        self.entity_id = ENTITY_ID_FORMAT.format(self._oid("malfunction"))

    @property
    def name(self) -> str:
        return self._with_addr(f"{self.larnitech_name} Malfunction")

    @property
    def is_on(self) -> bool | None:
        return self.status.get("malfunction") is not None

    @property
    def extra_state_attributes(self) -> dict | None:
        code = self.status.get("malfunction")
        return {"malfunction_code": code} if code is not None else None
