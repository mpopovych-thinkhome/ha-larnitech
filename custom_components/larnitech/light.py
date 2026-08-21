# Updated: 2026-08-21 10:34
"""Larnitech lights: lamp (on/off), dimmer-lamp (brightness), rgb-lamp (color).

lamp sub-types that are not lights (socket, lock, ...) are claimed by their own
platforms via `lamp_platform`. `level`, `saturation` AND `hue` are all 0-100
integer percent (hue = position around the color wheel as a percentage, not
degrees) — confirmed live 2026-08-14: red mapped correctly at hue=0, but
green/blue showed as orange/acid-yellow until hue was scaled ×3.6 to degrees.
Always write integers, never fractions."""
from __future__ import annotations

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_HS_COLOR,
    ENTITY_ID_FORMAT,
    ColorMode,
    LightEntity,
)
from homeassistant.core import callback

from .const import DOMAIN, lamp_platform
from .entity import LarnitechEntity

# `virtual` sub-types `lamp`/`dimer-lamp`/`rgb-lamp` deliberately NOT
# dispatched here — all three were tried and cancelled (user call
# 2026-08-21): the only live examples reported erroneous/unstable status.
# See const.py VIRTUAL_PLATFORM and larnitech_integration_spec.md "Virtual".


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
            return round(level / 100 * 255)
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
            level = round(kwargs[ATTR_BRIGHTNESS] / 255 * 100)
            await self.async_write_status({"level": level})
        else:
            await self.async_set_state(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.async_set_state(False)


class LarnitechRgb(LarnitechEntity, LightEntity):
    _attr_color_mode = ColorMode.HS
    _attr_supported_color_modes = {ColorMode.HS}

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
            return round(level / 100 * 255)
        if self.is_state_on:
            self._warn_once(
                "level",
                "Larnitech rgb-lamp %s: missing/non-numeric 'level' while on (status=%s)",
                self.entity_id,
                self.status,
            )
        return None

    @property
    def hs_color(self) -> tuple[float, float] | None:
        hue, saturation = self.status.get("hue"), self.status.get("saturation")
        if hue is None or saturation is None:
            if self.is_state_on:
                self._warn_once(
                    "hs",
                    "Larnitech rgb-lamp %s: missing hue/saturation while on (status=%s)",
                    self.entity_id,
                    self.status,
                )
            return None
        return (float(hue) * 3.6, float(saturation))

    async def async_turn_on(self, **kwargs) -> None:
        status = {}
        if ATTR_HS_COLOR in kwargs:
            hue, saturation = kwargs[ATTR_HS_COLOR]
            status["hue"] = round(hue / 3.6)
            status["saturation"] = round(saturation)
        if ATTR_BRIGHTNESS in kwargs:
            status["level"] = round(kwargs[ATTR_BRIGHTNESS] / 255 * 100)
        if status:
            await self.async_write_status(status)
        else:
            await self.async_set_state(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.async_set_state(False)
