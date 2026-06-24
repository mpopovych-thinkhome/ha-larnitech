# Updated: 2026-06-24 15:55
"""Larnitech lamps (on/off), added/removed dynamically."""
from __future__ import annotations

from homeassistant.components.light import ENTITY_ID_FORMAT, ColorMode, LightEntity
from homeassistant.core import callback

from .const import DOMAIN
from .entity import LarnitechEntity

TYPE = "lamp"


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    @callback
    def _add_new():
        current = {a for a, d in coordinator.data.items() if d.get("type") == TYPE}
        new = [LarnitechLamp(coordinator, a) for a in current - known]
        known.clear()
        known.update(current)
        if new:
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_listener(_add_new))
    _add_new()


class LarnitechLamp(LarnitechEntity, LightEntity):
    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}

    def __init__(self, coordinator, addr):
        super().__init__(coordinator, addr)
        self.entity_id = ENTITY_ID_FORMAT.format(self._slug)

    @property
    def is_on(self) -> bool:
        return self.status.get("state") == "on"

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.client.async_set_status(self._addr, {"state": "on"})
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.client.async_set_status(self._addr, {"state": "off"})
        await self.coordinator.async_request_refresh()
