# Updated: 2026-06-26 14:30
"""Larnitech lamp `air-fan` sub-type mapped to fan (on/off)."""
from __future__ import annotations

from homeassistant.components.fan import ENTITY_ID_FORMAT, FanEntity
from homeassistant.core import callback

from .const import DOMAIN, lamp_platform
from .entity import LarnitechEntity


def _claims(device: dict) -> bool:
    return device.get("type") == "lamp" and lamp_platform(device) == "fan"


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    @callback
    def _add_new():
        current = {a for a, d in coordinator.data.items() if _claims(d)}
        new = [LarnitechFan(coordinator, a) for a in current - known]
        known.clear()
        known.update(current)
        if new:
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_listener(_add_new))
    _add_new()


class LarnitechFan(LarnitechEntity, FanEntity):
    @property
    def is_on(self) -> bool:
        return self.is_state_on

    def __init__(self, coordinator, addr):
        super().__init__(coordinator, addr)
        self.entity_id = ENTITY_ID_FORMAT.format(self._slug)

    async def async_turn_on(self, percentage=None, preset_mode=None, **kwargs) -> None:
        await self.async_set_state(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.async_set_state(False)
