# Updated: 2026-06-26 14:30
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


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    @callback
    def _add_new():
        current = {a for a, d in coordinator.data.items() if d.get("type") in SENSORS}
        new = [
            LarnitechSensor(coordinator, a, *SENSORS[coordinator.data[a]["type"]])
            for a in current - known
        ]
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
