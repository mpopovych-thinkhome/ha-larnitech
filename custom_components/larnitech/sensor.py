# Updated: 2026-08-18 15:00
"""Larnitech measurement sensors (read-only), added/removed dynamically."""
from __future__ import annotations

from homeassistant.components.sensor import (
    ENTITY_ID_FORMAT,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    CONCENTRATION_PARTS_PER_MILLION,
    LIGHT_LUX,
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfTemperature,
)
from homeassistant.core import callback

from .const import DOMAIN
from .entity import LarnitechEntity

# Larnitech type -> (device_class, unit). All read a numeric `status.state`.
SENSORS = {
    "temperature-sensor": (SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS),
    "humidity-sensor": (SensorDeviceClass.HUMIDITY, PERCENTAGE),
    "co2-sensor": (SensorDeviceClass.CO2, CONCENTRATION_PARTS_PER_MILLION),
    "illumination-sensor": (SensorDeviceClass.ILLUMINANCE, LIGHT_LUX),
    "current-sensor": (SensorDeviceClass.CURRENT, UnitOfElectricCurrent.AMPERE),
}

# Larnitech type -> status key for a companion diagnostic sensor that rides
# the same device as another platform's primary entity for that addr (e.g.
# `climate-control`'s own entity lives in climate.py) — for a status field
# that primary entity's own domain has no slot for.
DIAGNOSTIC_SENSORS = {
    "climate-control": "pid-temperature",
}


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[tuple[str, str]] = set()

    @callback
    def _add_new():
        current = {(a, "measurement") for a, d in coordinator.data.items() if d.get("type") in SENSORS}
        current |= {
            (a, "pid") for a, d in coordinator.data.items() if d.get("type") in DIAGNOSTIC_SENSORS
        }
        new = []
        for addr, kind in current - known:
            if kind == "measurement":
                device_class, unit = SENSORS[coordinator.data[addr]["type"]]
                new.append(LarnitechSensor(coordinator, addr, device_class, unit))
            else:
                status_key = DIAGNOSTIC_SENSORS[coordinator.data[addr]["type"]]
                new.append(LarnitechPidSensor(coordinator, addr, status_key))
        known.clear()
        known.update(current)
        if new:
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_listener(_add_new))
    _add_new()


class LarnitechSensor(LarnitechEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, addr, device_class, unit):
        super().__init__(coordinator, addr)
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self.entity_id = ENTITY_ID_FORMAT.format(self._slug)

    @property
    def native_value(self):
        value = self.status.get("state")
        if isinstance(value, (int, float)):
            return value
        if value is not None:
            self._warn_once(
                f"state:{value!r}",
                "Larnitech sensor %s: non-numeric state %r (status=%s)",
                self.entity_id,
                value,
                self.status,
            )
        return None


class LarnitechPidSensor(LarnitechEntity, SensorEntity):
    """Companion diagnostic sensor for a climate device's PID heat/cool
    demand — signed percent, positive = heating, negative = cooling (sign
    not yet confirmed live — see climate.py `LarnitechClimateControl.hvac_action`).
    The `climate` domain has no numeric-percent slot for this, so it rides
    alongside the climate entity on the same device (shared `addr` -> same
    DeviceInfo, distinct unique_id)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:sine-wave"

    def __init__(self, coordinator, addr, status_key: str):
        super().__init__(coordinator, addr)
        self._status_key = status_key
        self._attr_unique_id = f"{self._slug}_pid"
        self.entity_id = ENTITY_ID_FORMAT.format(self._attr_unique_id)

    @property
    def name(self) -> str:
        return f"{super().name} PID demand"

    @property
    def native_value(self):
        value = self.status.get(self._status_key)
        return value if isinstance(value, (int, float)) else None
