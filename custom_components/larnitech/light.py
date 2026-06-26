# Updated: 2026-06-26 14:30
"""Larnitech lights: lamp (on/off), dimmer-lamp (brightness), rgb-lamp (color).

lamp sub-types that are not lights (socket, lock, ...) are claimed by their own
platforms via `lamp_platform`. The dimmer `level` (0.0-1.0) and rgb `r/g/b`
status/write keys are PROVISIONAL until confirmed against a live device."""
from __future__ import annotations

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ENTITY_ID_FORMAT,
    ColorMode,
    LightEntity,
)
from homeassistant.core import callback

from .const import DOMAIN, lamp_platform
from .entity import LarnitechEntity


def _light_class(device: dict):
    dtype = device.get("type")
    if dtype == "dimmer-lamp":
        return LarnitechDimmer
    if dtype == "rgb-lamp":
        return LarnitechRgb
    if dtype == "lamp" and lamp_platform(device) == "light":
        return LarnitechLamp
    return None


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    @callback
    def _add_new():
        current = {a for a, d in coordinator.data.items() if _light_class(d)}
        new = [
            _light_class(coordinator.data[a])(coordinator, a) for a in current - known
        ]
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
        return self.is_state_on

    async def async_turn_on(self, **kwargs) -> None:
        await self.async_set_state(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.async_set_state(False)


class LarnitechDimmer(LarnitechEntity, LightEntity):
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator, addr):
        super().__init__(coordinator, addr)
        self.entity_id = ENTITY_ID_FORMAT.format(self._slug)

    @property
    def is_on(self) -> bool:
        return self.is_state_on

    @property
    def brightness(self) -> int | None:
        level = self.status.get("level")
        if isinstance(level, (int, float)):
            return round(level * 255)
        if self.is_state_on:
            self._warn_once(
                "level",
                "Larnitech dimmer %s: missing/non-numeric 'level' while on (status=%s)",
                self.entity_id,
                self.status,
            )
        return None

    async def async_turn_on(self, **kwargs) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            level = round(kwargs[ATTR_BRIGHTNESS] / 255, 3)
            await self.coordinator.client.async_set_status(self._addr, {"level": level})
            await self.coordinator.async_request_refresh()
        else:
            await self.async_set_state(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.async_set_state(False)


class LarnitechRgb(LarnitechEntity, LightEntity):
    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}

    def __init__(self, coordinator, addr):
        super().__init__(coordinator, addr)
        self.entity_id = ENTITY_ID_FORMAT.format(self._slug)

    @property
    def is_on(self) -> bool:
        return self.is_state_on

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        r, g, b = self.status.get("r"), self.status.get("g"), self.status.get("b")
        if None in (r, g, b):
            if self.is_state_on:
                self._warn_once(
                    "rgb",
                    "Larnitech rgb-lamp %s: missing r/g/b while on (status=%s)",
                    self.entity_id,
                    self.status,
                )
            return None
        return (int(r), int(g), int(b))

    async def async_turn_on(self, **kwargs) -> None:
        if ATTR_RGB_COLOR in kwargs:
            r, g, b = kwargs[ATTR_RGB_COLOR]
            await self.coordinator.client.async_set_status(
                self._addr, {"r": r, "g": g, "b": b}
            )
            await self.coordinator.async_request_refresh()
        else:
            await self.async_set_state(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.async_set_state(False)
