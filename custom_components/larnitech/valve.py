# Updated: 2026-08-27 15:39
"""Larnitech valve: `lamp` sub-types `valve-3` / `damper`, and the bare
`valve` type (main shutoff valve). The bare type has DIFFERENT read and
write vocabularies (confirmed live 2026-08-17 by probing the API directly,
bypassing HA — see `_STATE_WRITE`):
- Read: `opened`/`closed` (participle) — confirmed live 2026-08-14.
- Write: `open`/`close` (imperative verb) — `status-set {"state":"opened"}`
  / `{"state":"closed"}` is REJECTED by the controller (`code 9: set-status
  has invalid parameter`); `on`/`off` (the original guess, by analogy with
  `lamp`) silently did nothing. `open`/`close` is ACCEPTED and actually
  moves the physical valve — verified: state read back as `opened`/`closed`
  after each."""
from __future__ import annotations

from homeassistant.components.valve import (
    ENTITY_ID_FORMAT,
    ValveEntity,
    ValveEntityFeature,
)
from homeassistant.core import callback

from .const import DOMAIN, lamp_platform
from .entity import LarnitechEntity

_OPEN_STATES = {"on", "opened", "open"}


def _claims(device: dict) -> bool:
    if device.get("type") == "valve":
        return True
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

    entry.async_on_unload(coordinator.add_discovery_listener(_add_new))
    _add_new()


class LarnitechValve(LarnitechEntity, ValveEntity):
    # Keys the strings.json state label override (Larnitech's own wording:
    # "Opened"/"Closed", not HA's default "Open"/"Closed").
    _attr_translation_key = "larnitech_valve"
    _attr_reports_position = False
    _attr_supported_features = ValveEntityFeature.OPEN | ValveEntityFeature.CLOSE

    def __init__(self, coordinator, addr):
        super().__init__(coordinator, addr)
        self.entity_id = ENTITY_ID_FORMAT.format(self._oid())

    @property
    def is_closed(self) -> bool:
        return self.status.get("state") not in _OPEN_STATES

    async def async_open_valve(self, **kwargs) -> None:
        if self.device.get("type") == "valve":
            await self.async_write_status({"state": "open"})
        else:
            await self.async_set_state(True)

    async def async_close_valve(self, **kwargs) -> None:
        if self.device.get("type") == "valve":
            await self.async_write_status({"state": "close"})
        else:
            await self.async_set_state(False)
