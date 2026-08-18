# Updated: 2026-08-18 16:20
"""Larnitech number: bare `vent`'s CO2 setpoint (`status.target`). The entity
always exists for every `vent` widget — it goes `unavailable` rather than
disappearing when no CO2 automation is linked (per user decision 2026-08-18:
a stable entity_id beats one that appears/disappears as automations are
toggled). `status.current` (the live CO2 reading on the same widget) is NOT
exposed here — it's the same physical sensor already surfaced as its own
`co2-sensor` entity in sensor.py; duplicating it on this widget would just
be two entities for one reading. `state`/`fan` (speed) for `vent` stay in
climate.py (`LarnitechVent`) — this file is only the setpoint."""
from __future__ import annotations

from homeassistant.components.number import ENTITY_ID_FORMAT, NumberEntity
from homeassistant.const import UnitOfRatio
from homeassistant.core import callback

from .const import DOMAIN
from .entity import LarnitechEntity


def _claims(device: dict) -> bool:
    return device.get("type") == "vent"


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    @callback
    def _add_new():
        current = {a for a, d in coordinator.data.items() if _claims(d)}
        new = [LarnitechVentCo2Setpoint(coordinator, a) for a in current - known]
        known.clear()
        known.update(current)
        if new:
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_listener(_add_new))
    _add_new()


class LarnitechVentCo2Setpoint(LarnitechEntity, NumberEntity):
    """CO2 setpoint for a `vent` widget with a linked CO2 sensor. Range/step
    (400-2000 ppm, step 50) are not documented anywhere — a reasonable guess
    for indoor CO2 targets, not yet confirmed against a live write."""

    _attr_native_min_value = 400
    _attr_native_max_value = 2000
    _attr_native_step = 50
    _attr_native_unit_of_measurement = UnitOfRatio.PARTS_PER_MILLION
    _attr_icon = "mdi:molecule-co2"

    def __init__(self, coordinator, addr):
        super().__init__(coordinator, addr)
        self._attr_unique_id = f"{self._slug}_co2_setpoint"
        self.entity_id = ENTITY_ID_FORMAT.format(self._attr_unique_id)

    @property
    def name(self) -> str:
        return f"{super().name} CO2 setpoint"

    @property
    def available(self) -> bool:
        # No linked CO2 automation right now -> `target` is absent -> the
        # setpoint is meaningless, not just momentarily unknown.
        return super().available and isinstance(self.status.get("target"), (int, float))

    @property
    def native_value(self):
        value = self.status.get("target")
        return value if isinstance(value, (int, float)) else None

    async def async_set_native_value(self, value: float) -> None:
        await self.async_write_status({"target": round(value)})
