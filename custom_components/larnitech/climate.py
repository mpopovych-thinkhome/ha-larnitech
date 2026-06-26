# Updated: 2026-06-26 14:30
"""Larnitech climate: valve-heating (+warm-floor), climate-control, AC.

Read keys are confirmed from live stand payloads. Write commands (state / mode /
target / setpoint-heat / setpoint-cool / fan / automation) are PROVISIONAL until
verified against a live device."""
from __future__ import annotations

from homeassistant.components.climate import (
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ENTITY_ID_FORMAT,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import callback

from .const import DOMAIN
from .entity import LarnitechEntity

_LARNI_TO_HVAC = {
    "heat": HVACMode.HEAT,
    "cool": HVACMode.COOL,
    "dry": HVACMode.DRY,
    "fan": HVACMode.FAN_ONLY,
    "auto": HVACMode.AUTO,
}
_HVAC_TO_LARNI = {v: k for k, v in _LARNI_TO_HVAC.items()}


def _climate_class(device: dict):
    dtype = device.get("type")
    if dtype in ("AC", "conditioner", "fancoil"):
        return LarnitechAC
    if dtype == "climate-control":
        return LarnitechClimateControl
    if dtype == "valve-heating":
        return LarnitechValveHeating
    return None


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    @callback
    def _add_new():
        current = {a for a, d in coordinator.data.items() if _climate_class(d)}
        new = [
            _climate_class(coordinator.data[a])(coordinator, a)
            for a in current - known
        ]
        known.clear()
        known.update(current)
        if new:
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_listener(_add_new))
    _add_new()


class LarnitechClimateBase(LarnitechEntity, ClimateEntity):
    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    @property
    def current_temperature(self) -> float | None:
        for key in ("current", "current-temperature"):
            value = self.status.get(key)
            if isinstance(value, (int, float)):
                return value
        return None

    @property
    def preset_modes(self) -> list[str] | None:
        # `automations` is a device-level key, not part of `status`.
        modes = self.device.get("automations")
        return modes if isinstance(modes, list) else None

    @property
    def preset_mode(self) -> str | None:
        return self.status.get("automation")

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        await self.async_write_status({"automation": preset_mode})

    async def async_turn_on(self) -> None:
        await self.async_set_state(True)

    async def async_turn_off(self) -> None:
        await self.async_set_state(False)


class LarnitechValveHeating(LarnitechClimateBase):
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]

    def __init__(self, coordinator, addr):
        super().__init__(coordinator, addr)
        self.entity_id = ENTITY_ID_FORMAT.format(self._slug)
        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        if self.preset_modes:
            features |= ClimateEntityFeature.PRESET_MODE
        self._attr_supported_features = features

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.HEAT if self.is_state_on else HVACMode.OFF

    @property
    def target_temperature(self) -> float | None:
        value = self.status.get("target")
        return value if isinstance(value, (int, float)) else None

    async def async_set_temperature(self, **kwargs) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is not None:
            await self.async_write_status({"target": temp})

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self.async_set_state(hvac_mode != HVACMode.OFF)


class LarnitechClimateControl(LarnitechClimateBase):
    def __init__(self, coordinator, addr):
        super().__init__(coordinator, addr)
        self.entity_id = ENTITY_ID_FORMAT.format(self._slug)
        modes = [HVACMode.OFF]
        # `modes` is a device-level key listing the supported set (e.g. heat/cool).
        for mode in self.device.get("modes") or []:
            if mode in _LARNI_TO_HVAC:
                modes.append(_LARNI_TO_HVAC[mode])
        modes.append(HVACMode.AUTO)
        self._attr_hvac_modes = modes
        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        if self.preset_modes:
            features |= ClimateEntityFeature.PRESET_MODE
        self._attr_supported_features = features

    @property
    def hvac_mode(self) -> HVACMode:
        if not self.is_state_on:
            return HVACMode.OFF
        return _LARNI_TO_HVAC.get(self.status.get("mode"), HVACMode.AUTO)

    @property
    def target_temperature_low(self) -> float | None:
        value = self.status.get("setpoint-heat")
        return value if isinstance(value, (int, float)) else None

    @property
    def target_temperature_high(self) -> float | None:
        value = self.status.get("setpoint-cool")
        return value if isinstance(value, (int, float)) else None

    async def async_set_temperature(self, **kwargs) -> None:
        status = {}
        if (low := kwargs.get(ATTR_TARGET_TEMP_LOW)) is not None:
            status["setpoint-heat"] = low
        if (high := kwargs.get(ATTR_TARGET_TEMP_HIGH)) is not None:
            status["setpoint-cool"] = high
        if status:
            await self.async_write_status(status)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.async_set_state(False)
        else:
            await self.async_write_status(
                {"state": "on", "mode": _HVAC_TO_LARNI.get(hvac_mode, "auto")}
            )


class LarnitechAC(LarnitechClimateBase):
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.HEAT,
        HVACMode.COOL,
        HVACMode.DRY,
        HVACMode.FAN_ONLY,
        HVACMode.AUTO,
    ]
    _attr_fan_modes = ["auto", "low", "medium", "high"]

    def __init__(self, coordinator, addr):
        super().__init__(coordinator, addr)
        self.entity_id = ENTITY_ID_FORMAT.format(self._slug)
        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        if self.preset_modes:
            features |= ClimateEntityFeature.PRESET_MODE
        self._attr_supported_features = features

    @property
    def hvac_mode(self) -> HVACMode:
        if not self.is_state_on:
            return HVACMode.OFF
        return _LARNI_TO_HVAC.get(self.status.get("mode"), HVACMode.AUTO)

    @property
    def target_temperature(self) -> float | None:
        value = self.status.get("target")
        return value if isinstance(value, (int, float)) else None

    @property
    def fan_mode(self) -> str | None:
        return self.status.get("fan")

    async def async_set_temperature(self, **kwargs) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is not None:
            await self.async_write_status({"target": temp})

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self.async_write_status({"fan": fan_mode})

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.async_set_state(False)
        else:
            await self.async_write_status(
                {"state": "on", "mode": _HVAC_TO_LARNI.get(hvac_mode, "auto")}
            )
