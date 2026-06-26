# Updated: 2026-06-26 14:30
"""Larnitech covers: blinds / jalousie / gate.

Position scale is Larnitech `0.0` = open .. `1.0` = closed (inverse of HA, where
100 = open). The `target` write key and the `stop` command are PROVISIONAL,
inferred from a single live blinds payload; jalousie tilt is not implemented yet
(no sample available)."""
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

# Larnitech type -> (device_class, has_position).
COVERS = {
    "blinds": (CoverDeviceClass.SHADE, True),
    "jalousie": (CoverDeviceClass.BLIND, True),
    "gate": (CoverDeviceClass.GATE, False),
}


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    @callback
    def _add_new():
        current = {a for a, d in coordinator.data.items() if d.get("type") in COVERS}
        new = [
            LarnitechCover(coordinator, a, *COVERS[coordinator.data[a]["type"]])
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
            return None
        pos = self.status.get("position")
        if not isinstance(pos, (int, float)):
            return None
        return round((1 - pos) * 100)

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
        await self.async_write_status({"target": 0.0})

    async def async_close_cover(self, **kwargs) -> None:
        await self.async_write_status({"target": 1.0})

    async def async_set_cover_position(self, **kwargs) -> None:
        target = round(1 - kwargs[ATTR_POSITION] / 100, 3)
        await self.async_write_status({"target": target})

    async def async_stop_cover(self, **kwargs) -> None:
        await self.async_write_status({"state": "stop"})
