# Updated: 2026-09-10 17:35
"""Larnitech lamp sub-types mapped to switch (socket / pump / closing-switch),
plus `light-scheme` (all `ls-type` variants — the API exposes the same
`status.state` on/off shape regardless of variant; behavioral differences
between Scheme/Scene/Scene+/Scheme Rev/master-slave live on the controller,
not the protocol). `ls-type=2` ("activate-only") ignores `turn_off` on the
controller side; HA still shows the control, it's just a no-op there.

Also `script` — an Imerel script instance, which behaves like a `lamp`: a
real on/off `status.state` plus the same `auto-state` (auto mode) flag,
rather than being a one-shot trigger, so a switch is the honest control.

Whether switching one script on switches others off is per-object
configuration, not a property of the type — the demo case happens to wire
its house modes and seasons as mutually exclusive groups. Either way the
controller does it itself and the siblings' new states arrive as ordinary
events, so there is nothing to model here."""
from __future__ import annotations

from homeassistant.components.switch import (
    ENTITY_ID_FORMAT,
    SwitchDeviceClass,
    SwitchEntity,
)
from homeassistant.core import callback

from .const import DOMAIN, lamp_platform
from .entity import LarnitechEntity


def _claims(device: dict) -> bool:
    if device.get("type") in ("light-scheme", "script"):
        return True
    return device.get("type") == "lamp" and lamp_platform(device) == "switch"


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    @callback
    def _add_new():
        current = {a for a, d in coordinator.data.items() if _claims(d)}
        new = [LarnitechSwitch(coordinator, a) for a in current - known]
        known.clear()
        known.update(current)
        if new:
            async_add_entities(new)

    entry.async_on_unload(coordinator.add_discovery_listener(_add_new))
    _add_new()


class LarnitechSwitch(LarnitechEntity, SwitchEntity):
    def __init__(self, coordinator, addr):
        super().__init__(coordinator, addr)
        if self.device.get("sub-type") == "socket":
            self._attr_device_class = SwitchDeviceClass.OUTLET
        self.entity_id = ENTITY_ID_FORMAT.format(self._oid())

    @property
    def is_on(self) -> bool:
        return self.is_state_on

    async def async_turn_on(self, **kwargs) -> None:
        await self.async_set_state(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.async_set_state(False)
