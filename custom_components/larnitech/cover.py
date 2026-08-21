# Updated: 2026-08-20 15:05
"""Larnitech covers: blinds / jalousie / gate.

Two different control models, confirmed live 2026-08-20:
- `blinds` has a real `position`/`target` on a **0-100** scale, inverted vs HA:
  Larnitech `0` = open, `100` = closed. A moving motor can read slightly past
  either end stop, so the HA percentage is clamped.
- `jalousie`/`gate` have no position at all — only `state`, driven by the verb
  form (`open`/`close`). Writing the participle the status reports back
  (`opened`/`closed`) is acked but does nothing. Their percentage is derived
  from `state` for display only (see `STATE_POSITION`).

Jalousie tilt is not implemented yet (no sample available)."""
from __future__ import annotations

from homeassistant.components.cover import (
    ATTR_POSITION,
    ENTITY_ID_FORMAT,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import callback

from .const import DOMAIN
from .entity import LarnitechEntity

# Larnitech type -> (device_class, has_position). Only `blinds` carries a
# position; the others are state-only (see module docstring).
COVERS = {
    "blinds": (CoverDeviceClass.SHADE, True),
    "jalousie": (CoverDeviceClass.BLIND, False),
    "gate": (CoverDeviceClass.GATE, False),
}

# `state` -> read-only percentage for the position-less types. HA's cover
# domain has no "partially open" state, so Larnitech's `middle` is surfaced
# as 50% — indication only, these types never get SET_POSITION.
STATE_POSITION = {
    "opened": 100,
    "open": 100,
    "middle": 50,
    "closed": 0,
}

# `virtual` sub-types `jalousie`/`gate`(+`120`) are NOT dispatched here:
# confirmed live 2026-08-20 their status carries no `state` at all, only an
# undocumented `hex` field — the verb-form open/close model below does not
# apply to them. See `larnitech_integration_spec.md` "Virtual" table.


def _cover_config(device: dict):
    return COVERS.get(device.get("type"))


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    @callback
    def _add_new():
        current = {a for a, d in coordinator.data.items() if _cover_config(d)}
        new = [
            LarnitechCover(coordinator, a, *_cover_config(coordinator.data[a]))
            for a in current - known
        ]
        known.clear()
        known.update(current)
        if new:
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_listener(_add_new))
    _add_new()


class LarnitechCover(LarnitechEntity, CoverEntity):
    def __init__(self, coordinator, addr, device_class, has_position):
        super().__init__(coordinator, addr)
        self._attr_device_class = device_class
        self._has_position = has_position
        features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
        )
        if has_position:
            features |= CoverEntityFeature.SET_POSITION
        self._attr_supported_features = features
        self.entity_id = ENTITY_ID_FORMAT.format(self._slug)

    @property
    def current_cover_position(self) -> int | None:
        if not self._has_position:
            return STATE_POSITION.get(self.status.get("state"))
        pos = self.status.get("position")
        if not isinstance(pos, (int, float)):
            return None
        return max(0, min(100, round(100 - pos)))

    @property
    def is_closed(self) -> bool | None:
        pos = self.current_cover_position
        if pos is not None:
            return pos == 0
        state = self.status.get("state")
        if state in ("opened", "open"):
            return False
        if state == "closed":
            return True
        return None

    @property
    def is_opening(self) -> bool:
        return self.status.get("state") == "opening"

    @property
    def is_closing(self) -> bool:
        return self.status.get("state") == "closing"

    async def async_open_cover(self, **kwargs) -> None:
        if self._has_position:
            await self.async_write_status({"target": 0})
        else:
            await self.async_write_status({"state": "open"})

    async def async_close_cover(self, **kwargs) -> None:
        if self._has_position:
            await self.async_write_status({"target": 100})
        else:
            await self.async_write_status({"state": "close"})

    async def async_set_cover_position(self, **kwargs) -> None:
        target = 100 - kwargs[ATTR_POSITION]
        await self.async_write_status({"target": target})

    async def async_stop_cover(self, **kwargs) -> None:
        await self.async_write_status({"state": "stop"})
