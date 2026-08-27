# Updated: 2026-08-27 15:39
"""Larnitech fan: `lamp/air-fan` only (on/off).

Bare `vent` and `virtual` sub-type `ventilation` both moved to climate.py —
both carry more than plain on/off+speed once a linked automation/sensor is
configured (CO2 automation for `vent`: manual/always-off/named presets,
same scheme as `valve-heating`/`fancoil`; temperature sensor for
`ventilation`), which `fan` has no room for and `climate` does."""
from __future__ import annotations

from homeassistant.components.fan import ENTITY_ID_FORMAT, FanEntity, FanEntityFeature
from homeassistant.core import callback

from .const import DOMAIN, lamp_platform
from .entity import LarnitechEntity


def _fan_class(device: dict):
    dtype = device.get("type")
    if dtype == "lamp" and lamp_platform(device) == "fan":
        return LarnitechFan
    return None


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    @callback
    def _add_new():
        current = {a for a, d in coordinator.data.items() if _fan_class(d)}
        new = [_fan_class(coordinator.data[a])(coordinator, a) for a in current - known]
        known.clear()
        known.update(current)
        if new:
            async_add_entities(new)

    entry.async_on_unload(coordinator.add_discovery_listener(_add_new))
    _add_new()


class LarnitechFan(LarnitechEntity, FanEntity):
    # TURN_ON/TURN_OFF must be declared explicitly — HA rejects the service
    # call as unsupported otherwise, even with async_turn_on/off implemented
    # (confirmed live 2026-08-18, same class of bug already handled in
    # climate.py's supported_features, missed here until now).
    _attr_supported_features = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF

    @property
    def is_on(self) -> bool:
        return self.is_state_on

    def __init__(self, coordinator, addr):
        super().__init__(coordinator, addr)
        self.entity_id = ENTITY_ID_FORMAT.format(self._oid())

    async def async_turn_on(self, percentage=None, preset_mode=None, **kwargs) -> None:
        await self.async_set_state(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.async_set_state(False)
