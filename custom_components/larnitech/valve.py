# Updated: 2026-06-26 14:30
"""Larnitech lamp `valve-3` / `damper` sub-types mapped to valve (open/close)."""
from __future__ import annotations

from homeassistant.components.valve import (
    ENTITY_ID_FORMAT,
    ValveEntity,
    ValveEntityFeature,
)
from homeassistant.core import callback

from .const import DOMAIN, lamp_platform
from .entity import LarnitechEntity


def _claims(device: dict) -> bool:
    return device.get("type") == "lamp" and lamp_platform(device) == "valve"


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    @callback
    def _add_new():
        current = {a for a, d in coordinator.data.items() if _claims(d)}
        new = [LarnitechValve(coordinator, a) for a in current - known]
        known.clear()
        known.update(current)
        if new:
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_listener(_add_new))
    _add_new()


class LarnitechValve(LarnitechEntity, ValveEntity):
    _attr_reports_position = False
    _attr_supported_features = ValveEntityFeature.OPEN | ValveEntityFeature.CLOSE

    def __init__(self, coordinator, addr):
        super().__init__(coordinator, addr)
        self.entity_id = ENTITY_ID_FORMAT.format(self._slug)

    @property
    def is_closed(self) -> bool:
        return not self.is_state_on

    async def async_open_valve(self, **kwargs) -> None:
        await self.async_set_state(True)

    async def async_close_valve(self, **kwargs) -> None:
        await self.async_set_state(False)
