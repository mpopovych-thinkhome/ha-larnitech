# Updated: 2026-08-21 19:10
"""Larnitech climate: valve-heating (+warm-floor), fancoil, climate-control, AC/conditioner.

Read keys are confirmed from live stand payloads. Write commands (state / mode /
target / setpoint-heat / setpoint-cool / fan / automation) are PROVISIONAL until
verified against a live device."""
from __future__ import annotations

import asyncio

from homeassistant.components.climate import (
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ENTITY_ID_FORMAT,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
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

# `climate-control`'s `mode` is its own (smaller) vocabulary — `auto` means
# "unconstrained", mapped to HEAT_COOL (this type already exposes dual
# setpoint-heat/setpoint-cool, matching HEAT_COOL's HA semantics), not
# HVACMode.AUTO like `_LARNI_TO_HVAC` maps it for AC/fancoil.
_CC_MODE_TO_HVAC = {"heat": HVACMode.HEAT, "cool": HVACMode.COOL, "auto": HVACMode.HEAT_COOL}
_CC_HVAC_TO_MODE = {v: k for k, v in _CC_MODE_TO_HVAC.items()}

# `hvac_action` for fancoil/AC/conditioner/valve-heating: none of these expose
# a live demand/PID signal like `climate-control`'s `pid-temperature`
# (confirmed live 2026-08-17 — fancoil's status is only `state`/`current`/
# `fan`/`mode`; AC/conditioner have no such field per the API2 doc either) —
# `hvac_action` mirrors the selected `mode` instead. Not new information
# (`hvac_mode` already carries it), but fills the attribute HA's climate
# cards look for. `"auto"` has no direct action (unknown whether it's
# currently heating or cooling) -> falls through to `None`.
_MODE_TO_ACTION = {
    "heat": HVACAction.HEATING,
    "cool": HVACAction.COOLING,
    "dry": HVACAction.DRYING,
    "fan": HVACAction.FAN,
}

# `valve-heating` (+ `warm-floor`) and `fancoil` always have at least two modes,
# confirmed live 2026-08-14: "manual" (plain on/off, no automation/preset
# involved — `status.automation` key is simply absent) and "always-off"
# (reserved value, locks the device off; can't be turned on except by
# switching to another automation/preset). Any number of user-defined named
# presets (Eco/Comfort/Boost/...) with their own temperature setpoint are
# optional on top of those two. Neither is in the XML-defined `automations`
# preset list, but `status.automation` reports them the same way a named
# preset would — both must be injected into `preset_modes`, or a device in
# either mode gets a preset_mode outside its own declared preset list (same
# class of bug as the `fan_mode` one). MANUAL is a synthetic label (Larnitech
# itself has no name for this state — the key is simply absent), and the
# "Always-off" shown in HA is deliberately capitalized for readability next
# to Comfort/Eco — the raw wire value (read and written) is lowercase
# `ALWAYS_OFF_RAW`. `conditioner`/`AC`/`climate-control` do NOT have this
# scheme (confirmed: `conditioner` has no `automation`/`automations` at all)
# — don't extend it to `LarnitechClimateBase` generally.
MANUAL = "Manual"
ALWAYS_OFF_RAW = "always-off"
ALWAYS_OFF = "Always-off"

# Turning a preset-driven type off from HA takes TWO writes — clear
# `automation`, then set `state: "off"` — and they must not be back to back.
# Confirmed live 2026-08-18 on `valve-heating` 1:5: both keys in one
# status-set, or two writes sent immediately after one another, both end with
# `automation` cleared but `state` still ON. Clearing the automation makes the
# controller re-evaluate the channel and drive it back to its manual state,
# and that re-evaluation lands AFTER an "off" that arrives too soon. The same
# pair spaced a few seconds apart works. 1s settle window (per user decision
# 2026-08-18) — verified live at this value, not a measured minimum.
_PRESET_RESET_SETTLE = 1

# `fancoil`: switching `hvac_mode` between heat/cool needs `mode` written
# first, then `state: "on"` a beat later — not the same call (per user
# instruction 2026-08-18). Separate constant from `_PRESET_RESET_SETTLE`
# despite the same value: different mechanism (mode-before-state ordering
# on turn-on, not an automation-reset race on turn-off).
_MODE_THEN_STATE_SETTLE = 1

# `AC`/`conditioner` capability bitmasks — bit set = that option exists.
# Masks reach the API as device-level keys (hex strings, e.g. "0x1A") when
# explicitly configured in XML — confirmed for `modes` and `vane-hor`. The
# exception is the fan mask (`fans`/`funs`), which never appears over the API
# even when set (verified repeatedly 2026-08-14 on a unit carrying
# `fans="0x47"`), so it always falls back to the default.
# Any absent mask falls back to the vendor's documented per-type default below
# (wiki.larnitech.com/AC and /Conditioner) — note the defaults are NOT
# "everything": an AC has no horizontal vanes (0x00) and no Auto vertical
# vane (0x7E), a conditioner has no "sides to center" horizontal (0x7F).
# The conditioner's fan mask is spelled `funs` in the vendor's own docs —
# that's their typo, but it is the real attribute name (a conditioner with
# `fans=` set is silently ignored and falls back to the default).
_AC_DEFAULTS = {"modes": 0x1F, "fans": 0x1F, "vane-ver": 0x7E, "vane-hor": 0x00}
_CONDITIONER_DEFAULTS = {"modes": 0x1F, "funs": 0x0F, "vane-ver": 0x7F, "vane-hor": 0x7F}

_MODE_BITS = [
    (0, HVACMode.FAN_ONLY),
    (1, HVACMode.COOL),
    (2, HVACMode.DRY),
    (3, HVACMode.HEAT),
    (4, HVACMode.AUTO),
]

# Fan speeds: the WIRE vocabulary is a fixed set of names, NOT the numbered
# scheme the masks/UI describe — probed exhaustively against both live
# devices 2026-08-14, `status-set` rejects anything else with
# "set-status has invalid parameter" (including bare numbers and "silent").
# So bits 0-3 are addressable and bits 4-6 (4th/5th speed, silent mode) have
# no known wire value — they're offered by neither list until one turns up.
_FAN_WIRE = ["auto", "low", "middle", "high"]
_FAN_LABELS = ["Auto", "1st Speed", "2nd Speed", "3rd Speed"]

_VANE_HOR_MODES = [
    "Left", "Left-Center", "Center", "Center-Right", "Right",
    "Sides (Low Angle)", "Sides (High Angle)", "Sides To Center",
]
_VANE_VER_MODES = ["Auto", "Top", "Top-Center", "Center", "Center-Bottom", "Bottom", "Swing"]


def _mask_filter(bits: int, table: list[str]) -> list[str]:
    """Entries of `table` whose bit position is set in `bits`."""
    return [name for idx, name in enumerate(table) if bits & (1 << idx)]


class _ManualAlwaysOffPresets:
    """Mixin for `valve-heating` and `fancoil`: injects the two reserved modes
    (see module docstring above) into preset_modes/preset_mode/writes, on top
    of whatever `LarnitechClimateBase.preset_modes` finds in `automations`."""

    @property
    def preset_modes(self) -> list[str]:
        modes = list(super().preset_modes or [])
        for mode in (MANUAL, ALWAYS_OFF):
            if mode not in modes:
                modes.append(mode)
        return modes

    @property
    def preset_mode(self) -> str:
        raw = self.status.get("automation")
        if raw == ALWAYS_OFF_RAW:
            return ALWAYS_OFF
        return raw or MANUAL

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        # An empty value to go manual is confirmed live (2026-08-18) — the
        # controller drops the `automation` key entirely. Writing ALWAYS_OFF
        # back as the raw lowercase string is still untested.
        if preset_mode == MANUAL:
            value = ""
        elif preset_mode == ALWAYS_OFF:
            value = ALWAYS_OFF_RAW
        else:
            value = preset_mode
        await self.async_write_status({"automation": value})


def _climate_class(device: dict):
    dtype = device.get("type")
    if dtype == "AC":
        return LarnitechAC
    if dtype == "conditioner":
        return LarnitechConditioner
    if dtype == "fancoil":
        return LarnitechFancoil
    if dtype == "climate-control":
        return LarnitechClimateControl
    if dtype == "valve-heating":
        return LarnitechValveHeating
    if dtype == "virtual" and device.get("sub-type") == "ventilation":
        return LarnitechVentilation
    if dtype == "vent":
        return LarnitechVent
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
    # Setpoints are integer-only across every Larnitech climate device — no
    # fractional target temperatures, ever.
    _attr_target_temperature_step = 1

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


class LarnitechValveHeating(_ManualAlwaysOffPresets, LarnitechClimateBase):
    # Keys the icons.json lookup for the Manual / Always-off preset icons
    # (icon translations are keyed by translation_key, not by domain).
    _attr_translation_key = "valve_heating"
    # No HEAT: the widget is a bare on/off actuator with no direction of its
    # own — even in Manual, nothing in the API says "heat" beyond the type
    # name. HEAT_COOL is the honest label for "on", both here and (per
    # `hvac_mode`) when a named preset means something else entirely (e.g.
    # climate-control) is driving it through its own automation.
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT_COOL]

    _BASE_FEATURES = (
        ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.PRESET_MODE
    )

    def __init__(self, coordinator, addr):
        super().__init__(coordinator, addr)
        self.entity_id = ENTITY_ID_FORMAT.format(self._oid())

    @property
    def supported_features(self) -> ClimateEntityFeature:
        # `target` is only meaningful under a named preset — that's the
        # setpoint its automation actually regulates toward. In Manual /
        # Always-off there IS no automation reading `target`; the widget is
        # a plain on/off switch regardless of what `target` last held, on or
        # off doesn't change that — so gate on the preset, not hvac_mode.
        if self._custom_preset_active:
            return self._BASE_FEATURES | ClimateEntityFeature.TARGET_TEMPERATURE
        return self._BASE_FEATURES

    @property
    def _custom_preset_active(self) -> bool:
        """True when a named preset (not Manual, not Always-off) is assigned —
        the zone is actively managed by its own automation."""
        return self.preset_mode not in (MANUAL, ALWAYS_OFF)

    @property
    def hvac_mode(self) -> HVACMode:
        # With a named preset assigned, hvac_mode reflects that assignment,
        # not the moment-to-moment on/off of the heating channel — the
        # channel instead drives `hvac_action` below. Without one (Manual /
        # Always-off), hvac_mode is the on/off channel itself. Either way:
        # HEAT_COOL, never HEAT — see `_attr_hvac_modes`.
        if self._custom_preset_active:
            return HVACMode.HEAT_COOL
        return HVACMode.HEAT_COOL if self.is_state_on else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction | None:
        # A named preset is the valve's OWN automation, regulating toward its
        # own `target` — that IS heating, by definition of what the preset
        # system does. Manual means the on/off channel was written directly,
        # with no automation on THIS widget involved — most likely something
        # else (e.g. climate-control) using the valve as its own actuator,
        # which is just as often for the cool side of a zone as the hot one
        # (see class docstring). None is HA's convention for "action not
        # known" — the honest label when the purpose behind "on" isn't this
        # widget's to know.
        if self._custom_preset_active:
            return HVACAction.HEATING if self.is_state_on else HVACAction.IDLE
        return None if self.is_state_on else HVACAction.OFF

    @property
    def target_temperature(self) -> float | None:
        value = self.status.get("target")
        return round(value) if isinstance(value, (int, float)) else None

    async def async_set_temperature(self, **kwargs) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is not None:
            await self.async_write_status({"target": round(temp)})

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode != HVACMode.OFF:
            await self.async_write_status({"state": "on"})
            return
        # Turning off while a named preset is active must also drop the
        # preset back to Manual — otherwise `hvac_mode` above stays locked to
        # HEAT_COOL (custom preset overrides the on/off channel), so an "Off"
        # from HA would silently do nothing visible in the UI.
        #
        # Two spaced-out writes, automation first — see `_PRESET_RESET_SETTLE`.
        if self._custom_preset_active:
            await self.async_write_status({"automation": ""})
            await asyncio.sleep(_PRESET_RESET_SETTLE)
        await self.async_write_status({"state": "off"})


class LarnitechFancoil(_ManualAlwaysOffPresets, LarnitechClimateBase):
    """Fan speed (`status.fan`) is always a 0-100 percent float — confirmed
    live 2026-08-14 on both a percentage unit and one explicitly configured
    as 3-speed ("Fancoil 3sp"), so there's no stepped-vs-percentage branch to
    handle. Exposed as 10%-step fan_modes ("0%".."100%"); a raw reading is
    rounded to the nearest step (33 -> "30%", 66 -> "70%")."""

    # Keys the icons.json lookup for the Manual / Always-off preset icons and
    # the 10%-step fan-speed icons.
    _attr_translation_key = "fancoil"
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL]
    _attr_fan_modes = [f"{pct}%" for pct in range(0, 101, 10)]

    def __init__(self, coordinator, addr):
        super().__init__(coordinator, addr)
        self.entity_id = ENTITY_ID_FORMAT.format(self._oid())
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.PRESET_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )

    @property
    def hvac_mode(self) -> HVACMode:
        if not self.is_state_on:
            return HVACMode.OFF
        return _LARNI_TO_HVAC.get(self.status.get("mode"), HVACMode.HEAT)

    @property
    def hvac_action(self) -> HVACAction | None:
        if not self.is_state_on:
            return HVACAction.OFF
        # No live demand/PID signal for fancoil (confirmed live 2026-08-17 —
        # `status` is only `state`/`current`/`fan`/`mode`) — mirrors the
        # selected `mode`, same info `hvac_mode` above already carries.
        return _MODE_TO_ACTION.get(self.status.get("mode"), HVACAction.HEATING)

    @property
    def target_temperature(self) -> float | None:
        value = self.status.get("target")
        return round(value) if isinstance(value, (int, float)) else None

    @property
    def fan_mode(self) -> str | None:
        fan = self.status.get("fan")
        if isinstance(fan, (int, float)):
            step = min(100, max(0, round(fan / 10) * 10))
            return f"{step}%"
        if self.is_state_on:
            self._warn_once(
                "fan",
                "Larnitech fancoil %s: missing/non-numeric 'fan' while on (status=%s)",
                self.entity_id,
                self.status,
            )
        return None

    async def async_set_temperature(self, **kwargs) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is not None:
            await self.async_write_status({"target": round(temp)})

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self.async_write_status({"fan": int(fan_mode.rstrip("%"))})

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode != HVACMode.OFF:
            # Mode first, state second — see `_MODE_THEN_STATE_SETTLE`.
            await self.async_write_status(
                {"mode": _HVAC_TO_LARNI.get(hvac_mode, "heat")}
            )
            await asyncio.sleep(_MODE_THEN_STATE_SETTLE)
            await self.async_write_status({"state": "on"})
            return
        # Same reasoning as `LarnitechValveHeating`, including the spacing:
        # a named preset must drop back to Manual on turn-off, and the "off"
        # has to wait out the reset (see `_PRESET_RESET_SETTLE`).
        if self.preset_mode not in (MANUAL, ALWAYS_OFF):
            await self.async_write_status({"automation": ""})
            await asyncio.sleep(_PRESET_RESET_SETTLE)
        await self.async_write_status({"state": "off"})


class LarnitechClimateControl(LarnitechClimateBase):
    """`mode` (live-tested 2026-08-18 by probing the API directly, bypassing
    HA) does NOT work like AC/fancoil's `mode` — it isn't informational, it
    CONSTRAINS what the *active automation* is allowed to do: setting
    `mode: "heat"` on a heat+cool automation locks it to heat only; setting a
    mode the active automation doesn't support at all goes dead (no
    setpoint, no `pid-temperature`, no error either) until corrected.
    Vocabulary is `heat`/`cool`/`auto` (`auto` = unconstrained — confirmed
    live: an empty-string write doesn't clear the key, it settles on
    `mode: "auto"`), mapped to `hvac_mode` as heat/cool/heat_cool.

    `hvac_modes` is always the full `[off, heat_cool, heat, cool]` — per
    user decision 2026-08-18, offering fewer based on the device-level
    `modes` capability list (a list of strings like `["cool"]`, NOT the hex
    bitmask `AC`/`conditioner` use for the same key name) was tried and
    dropped: picking an incompatible mode is the user's call to make, not
    something to hide from the UI. Presets are custom-only: unlike
    `valve-heating`/`fancoil` there's no manual/always-off pair here.

    Which setpoint(s) exist depends on what the *active* automation actually
    controls, so `supported_features`/target-temperature are read live from
    `status` on every update rather than frozen at entity creation: both
    `setpoint-heat` and `setpoint-cool` present -> dual range; only one ->
    single `target_temperature` using whichever key is there; neither ->
    no temperature control offered at all."""

    # Keys the icons.json lookup for the preset_mode icon (preset names here
    # are arbitrary per-object automation names, not a fixed vocabulary like
    # fancoil/valve-heating's Manual/Always-off — one default icon for all).
    _attr_translation_key = "climate_control"
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT_COOL, HVACMode.HEAT, HVACMode.COOL]

    def __init__(self, coordinator, addr):
        super().__init__(coordinator, addr)
        self.entity_id = ENTITY_ID_FORMAT.format(self._oid())

    @property
    def _has_heat(self) -> bool:
        return isinstance(self.status.get("setpoint-heat"), (int, float))

    @property
    def _has_cool(self) -> bool:
        return isinstance(self.status.get("setpoint-cool"), (int, float))

    @property
    def supported_features(self) -> ClimateEntityFeature:
        features = ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        if self._has_heat and self._has_cool:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        elif self._has_heat or self._has_cool:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        if self.preset_modes:
            features |= ClimateEntityFeature.PRESET_MODE
        return features

    @property
    def hvac_mode(self) -> HVACMode:
        if not self.is_state_on:
            return HVACMode.OFF
        return _CC_MODE_TO_HVAC.get(self.status.get("mode"), HVACMode.HEAT_COOL)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.async_set_state(False)
            return
        await self.async_write_status(
            {"state": "on", "mode": _CC_HVAC_TO_MODE.get(hvac_mode, "auto")}
        )

    @property
    def hvac_action(self) -> HVACAction | None:
        if not self.is_state_on:
            return HVACAction.OFF
        # `pid-temperature`: heat/cool demand, signed — positive = heating,
        # negative = cooling, 0 = idle (not yet confirmed live; stand was
        # offline for this session — verify sign against a real zone before
        # relying on it).
        pid = self.status.get("pid-temperature")
        if isinstance(pid, (int, float)):
            if pid > 0:
                return HVACAction.HEATING
            if pid < 0:
                return HVACAction.COOLING
            return HVACAction.IDLE
        # Fallback if `pid-temperature` is absent: same heat/cool lexicon as
        # AC/fancoil's `mode`.
        mode = self.status.get("mode")
        if mode == "heat":
            return HVACAction.HEATING
        if mode == "cool":
            return HVACAction.COOLING
        return None

    @property
    def target_temperature(self) -> float | None:
        # Single-setpoint case only — the dual case uses low/high below.
        if self._has_heat and self._has_cool:
            return None
        for key in ("setpoint-heat", "setpoint-cool"):
            value = self.status.get(key)
            if isinstance(value, (int, float)):
                return round(value)
        return None

    @property
    def target_temperature_low(self) -> float | None:
        value = self.status.get("setpoint-heat")
        return round(value) if isinstance(value, (int, float)) else None

    @property
    def target_temperature_high(self) -> float | None:
        value = self.status.get("setpoint-cool")
        return round(value) if isinstance(value, (int, float)) else None

    async def async_set_temperature(self, **kwargs) -> None:
        status = {}
        if (low := kwargs.get(ATTR_TARGET_TEMP_LOW)) is not None:
            status["setpoint-heat"] = round(low)
        if (high := kwargs.get(ATTR_TARGET_TEMP_HIGH)) is not None:
            status["setpoint-cool"] = round(high)
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is not None:
            # Single-setpoint mode: write back whichever key is currently active.
            status["setpoint-cool" if self._has_cool else "setpoint-heat"] = round(temp)
        if status:
            await self.async_write_status(status)


class _LarnitechACBase(LarnitechClimateBase):
    """Shared AC/conditioner protocol shape (same status keys, same wire
    vocabulary for `fan` — confirmed identical live 2026-08-17: probed
    `status-set` with every candidate value on one of each type side by
    side, got byte-identical accept/reject results). What differs per type
    is capability-mask handling — each subclass supplies its own
    `_defaults`/`_fan_key`/`_mode_bits()`/`_fan_bits()`; kept as separate
    classes (not one class branching on `type`) so that per-type mask
    quirks (see `LarnitechConditioner`) stay local to their own class."""

    _defaults: dict[str, int]
    _fan_key: str

    def __init__(self, coordinator, addr):
        super().__init__(coordinator, addr)
        self.entity_id = ENTITY_ID_FORMAT.format(self._oid())

    def _mask(self, key: str) -> int:
        """Capability bitmask for `key`, falling back to the vendor default.

        `self.device` is the coordinator's last `get-devices status:detailed`
        snapshot — the exported XML backup is NOT the live truth, Larnitech's
        app can reconfigure a widget's mask at any time. Every mask-derived
        property below re-reads it live rather than freezing a value at
        entity creation: CoordinatorEntity re-runs `async_write_ha_state`
        (which re-reads these properties) on every coordinator update, both
        the periodic poll and a push-triggered refresh, so a mask changed on
        the Larnitech side is picked up within one poll interval — no HA
        restart needed."""
        raw = self.device.get(key)
        if isinstance(raw, str):
            try:
                return int(raw, 16)
            except ValueError:
                pass
        elif isinstance(raw, int):
            return raw
        return self._defaults[key]

    def _mode_bits(self) -> int:
        raise NotImplementedError

    def _fan_bits(self) -> int:
        raise NotImplementedError

    @property
    def hvac_modes(self) -> list[HVACMode]:
        mode_bits = self._mode_bits()
        return [HVACMode.OFF] + [
            mode for bit, mode in _MODE_BITS if mode_bits & (1 << bit)
        ]

    @property
    def fan_modes(self) -> list[str] | None:
        # Labels are what HA shows; the wire value is the same index in _FAN_WIRE.
        return _mask_filter(self._fan_bits(), _FAN_LABELS) or None

    @property
    def swing_horizontal_modes(self) -> list[str] | None:
        return _mask_filter(self._mask("vane-hor"), _VANE_HOR_MODES) or None

    @property
    def swing_modes(self) -> list[str] | None:
        return _mask_filter(self._mask("vane-ver"), _VANE_VER_MODES) or None

    @property
    def supported_features(self) -> ClimateEntityFeature:
        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        if self.preset_modes:
            features |= ClimateEntityFeature.PRESET_MODE
        if self.fan_modes:
            features |= ClimateEntityFeature.FAN_MODE
        if self.swing_horizontal_modes:
            features |= ClimateEntityFeature.SWING_HORIZONTAL_MODE
        if self.swing_modes:
            features |= ClimateEntityFeature.SWING_MODE
        return features

    @property
    def hvac_mode(self) -> HVACMode:
        if not self.is_state_on:
            return HVACMode.OFF
        return _LARNI_TO_HVAC.get(self.status.get("mode"), HVACMode.AUTO)

    @property
    def hvac_action(self) -> HVACAction | None:
        if not self.is_state_on:
            return HVACAction.OFF
        # No live demand/PID signal for AC/conditioner (per the API2 device
        # table) — mirrors the selected `mode`; "auto" has no direct action
        # (unknown whether it's currently heating or cooling) -> None.
        return _MODE_TO_ACTION.get(self.status.get("mode"))

    @property
    def target_temperature(self) -> float | None:
        value = self.status.get("target")
        if not isinstance(value, (int, float)):
            return None
        # Controller bug, confirmed live 2026-08-17: `status-set {"state":
        # "on", "mode": ...}` without a `target` in the SAME call resets the
        # setpoint to a sentinel out-of-range value (`-128` observed — looks
        # like an uninitialized signed byte, 0x80) and discards whatever
        # valid setpoint was there a moment before, even while the device was
        # off. `async_set_hvac_mode` below works around it by always
        # resending the target; this guards the read side against any
        # sentinel that slips through anyway (e.g. a write from outside HA).
        if not (self.min_temp <= value <= self.max_temp):
            return None
        return round(value)

    @property
    def fan_mode(self) -> str | None:
        fan = self.status.get("fan")
        if isinstance(fan, str) and fan in _FAN_WIRE:
            return _FAN_LABELS[_FAN_WIRE.index(fan)]
        if self.is_state_on and fan is not None:
            self._warn_once(
                "fan_mode",
                "Larnitech %s: unrecognized 'fan' value %r, expected one of %s (status=%s)",
                self.entity_id,
                fan,
                _FAN_WIRE,
                self.status,
            )
        return None

    @property
    def swing_horizontal_mode(self) -> str | None:
        return self._vane_mode("vane-hor", _VANE_HOR_MODES)

    @property
    def swing_mode(self) -> str | None:
        return self._vane_mode("vane-ver", _VANE_VER_MODES)

    def _vane_mode(self, key: str, table: list[str]) -> str | None:
        value = self.status.get(key)
        if isinstance(value, (int, float)):
            idx = int(value)
            if 0 <= idx < len(table):
                return table[idx]
        return None

    async def async_set_temperature(self, **kwargs) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is not None:
            await self.async_write_status({"target": round(temp)})

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        # The controller only accepts the wire names, never an index —
        # a number is rejected with "set-status has invalid parameter".
        await self.async_write_status({"fan": _FAN_WIRE[_FAN_LABELS.index(fan_mode)]})

    async def async_set_swing_horizontal_mode(self, swing_horizontal_mode: str) -> None:
        idx = _VANE_HOR_MODES.index(swing_horizontal_mode)
        await self.async_write_status({"vane-hor": idx})

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        idx = _VANE_VER_MODES.index(swing_mode)
        await self.async_write_status({"vane-ver": idx})

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.async_set_state(False)
            return
        # Must include `target` in the SAME call — see `target_temperature`
        # docstring above for why (controller resets the setpoint to a
        # sentinel if `state`/`mode` are written without it).
        status = {"state": "on", "mode": _HVAC_TO_LARNI.get(hvac_mode, "auto")}
        target = self.target_temperature
        status["target"] = target if target is not None else self.min_temp
        await self.async_write_status(status)


class LarnitechAC(_LarnitechACBase):
    """`type="AC"` — `modes`/`vane-hor` masks confirmed live 2026-08-17 to
    arrive correctly over the API and are used as-is. Only `fans` never
    arrives (confirmed again 2026-08-17 with `fans="0x77"` explicitly set on
    a real unit — still absent from `get-devices`), so it always falls back
    to the vendor default."""

    # Required for icons.json/strings.json state_attributes lookups
    # (swing_mode / swing_horizontal_mode) — icon/name translations are
    # keyed by translation_key, not domain, and there's no wildcard fallback
    # for custom integrations. Doesn't affect the entity's displayed name:
    # LarnitechEntity always returns an explicit `name` and sets
    # `_attr_has_entity_name = False`.
    _attr_translation_key = "ac"
    _defaults = _AC_DEFAULTS
    _fan_key = "fans"

    def _mode_bits(self) -> int:
        return self._mask("modes")

    def _fan_bits(self) -> int:
        return self._mask(self._fan_key)


class LarnitechConditioner(_LarnitechACBase):
    """`type="conditioner"` — Larnitech bug, confirmed live 2026-08-17:
    `modes` is sent back as a device-level key (unlike `funs`, which never
    arrives at all) but with the WRONG value: XML configured `modes="0x1A"`,
    API reported back "0x1F" (everything) regardless. Until Larnitech fixes
    this server-side, don't trust `modes`/`funs` for conditioner at all —
    always offer the full default set."""

    _attr_translation_key = "conditioner"
    _defaults = _CONDITIONER_DEFAULTS
    _fan_key = "funs"

    def _mode_bits(self) -> int:
        return self._defaults["modes"]

    def _fan_bits(self) -> int:
        return self._defaults[self._fan_key]


class LarnitechVentilation(LarnitechClimateBase):
    """`virtual` sub-type `ventilation` (Komfovent-class units) — no
    heat/cool selection, it's a ventilation fan with an optional linked
    temperature sensor and setpoint. `climate` domain fits better than `fan`
    here: `fan_mode` covers the speed preset natively, and current/target
    temperature have real slots instead of needing a companion sensor.

    `fan` preset vocabulary — `auto`/`low`/`middle`/`high` — confirmed live
    2026-08-18 by probing the API directly (write, then read back) on the
    test stand. Same 4 wire values `AC`/`fancoil` use, but duplicated here as
    a plain list rather than imported — not worth sharing across unrelated
    widget types for four strings.

    `current`/`target` only appear once the widget's `temperature-sensors`
    XML attribute is configured (confirmed live 2026-08-18) — `target`'s
    exact role (setpoint the ventilation tries to reach?) isn't confirmed
    yet, exposed provisionally."""

    _attr_translation_key = "ventilation"
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.FAN_ONLY]
    _attr_fan_modes = ["auto", "low", "middle", "high"]

    def __init__(self, coordinator, addr):
        super().__init__(coordinator, addr)
        self.entity_id = ENTITY_ID_FORMAT.format(self._oid())

    @property
    def supported_features(self) -> ClimateEntityFeature:
        features = (
            ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        if isinstance(self.status.get("target"), (int, float)):
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        return features

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.FAN_ONLY if self.is_state_on else HVACMode.OFF

    @property
    def fan_mode(self) -> str | None:
        fan = self.status.get("fan")
        if isinstance(fan, str) and fan in self._attr_fan_modes:
            return fan
        if self.is_state_on and fan is not None:
            self._warn_once(
                "fan",
                "Larnitech ventilation %s: unrecognized 'fan' preset %r (status=%s)",
                self.entity_id,
                fan,
                self.status,
            )
        return None

    @property
    def current_temperature(self) -> float | None:
        value = self.status.get("current")
        return value if isinstance(value, (int, float)) else None

    @property
    def target_temperature(self) -> float | None:
        value = self.status.get("target")
        return round(value) if isinstance(value, (int, float)) else None

    async def async_set_temperature(self, **kwargs) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is not None:
            await self.async_write_status({"target": round(temp)})

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self.async_write_status({"fan": fan_mode})

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self.async_set_state(hvac_mode != HVACMode.OFF)


class LarnitechVent(LarnitechClimateBase):
    """Bare `vent` type — moved to `climate` domain (was `fan`) once a CO2
    automation is linked: `automation`/`automations` follow the *exact* same
    manual/always-off/named scheme as `valve-heating`/`fancoil` (confirmed
    live 2026-08-18: absent `automation` = manual, `"always-off"` = locked
    off + `state` forced off, named automations from `automations` are
    optional presets on top) — written as its own small copy here rather
    than the shared `_ManualAlwaysOffPresets` mixin, so this stays isolated
    from the already-verified valve-heating/fancoil behavior.

    `status.target`/`current` are a CO2 setpoint/reading (ppm), confirmed
    live only once a named automation is active. Neither is exposed as
    climate current/target temperature — CO2 isn't temperature, and
    `climate` has no CO2 concept at all. `current` is the same physical
    sensor already exposed as its own `co2-sensor` entity; `target` is a
    separate writable `number` entity (`number.py`).

    `status.fan` (0-100%) is exposed as `fan_mode` in 10%-step presets
    (`"0%"`..`"100%"`), same shape as `fancoil`'s fan_modes but duplicated
    rather than shared — a few strings, not worth an abstraction.

    hvac_mode is on/off only (`vent` never heats/cools), but follows the
    same "named preset locks the mode" rule as `valve-heating`: while a
    named automation is assigned, `hvac_mode` stays FAN_ONLY regardless of
    the channel's moment-to-moment on/off — the channel and `fan` speed
    drive `hvac_action` instead. Turning off from HA while a named
    automation is active also resets it to Manual (`automation: ""`), or
    `hvac_mode` would stay locked to FAN_ONLY and the "Off" click would look
    like it did nothing."""

    _attr_translation_key = "vent"
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.FAN_ONLY]
    _attr_fan_modes = [f"{pct}%" for pct in range(0, 101, 10)]

    def __init__(self, coordinator, addr):
        super().__init__(coordinator, addr)
        self.entity_id = ENTITY_ID_FORMAT.format(self._oid())
        self._attr_supported_features = (
            ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.PRESET_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )

    @property
    def current_temperature(self) -> None:
        # Override the inherited `LarnitechClimateBase` reading: `status.current`
        # on this type is the CO2 ppm reading (confirmed live 2026-08-18), not a
        # temperature — surfacing it here showed "800°C" as current_temperature.
        # CO2 stays out of the climate domain entirely (see class docstring).
        return None

    @property
    def _custom_preset_active(self) -> bool:
        """True when a named automation (not Manual, not Always-off) is
        assigned — same concept as `LarnitechValveHeating`'s: the zone is
        actively managed by its own automation, so `hvac_mode` reflects that
        assignment rather than the moment-to-moment on/off of the channel —
        the channel (and `fan` speed) instead drive `hvac_action`. Without a
        named automation, `hvac_mode` follows the channel directly."""
        return self.preset_mode not in (MANUAL, ALWAYS_OFF)

    @property
    def hvac_mode(self) -> HVACMode:
        if self._custom_preset_active:
            return HVACMode.FAN_ONLY
        return HVACMode.FAN_ONLY if self.is_state_on else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction | None:
        # Two states only: moving air (FAN) or not (IDLE). `status.fan` alone
        # is NOT that signal — confirmed live 2026-08-18: with `state: "off"`
        # the controller still reports the last speed (`fan: 30`), so a
        # fan-only test showed FAN on a stopped unit. The channel has to be on
        # AND the speed above zero. `hvac_mode` above carries the
        # on/off-vs-locked-by-preset distinction, so this never returns OFF.
        fan = self.status.get("fan")
        running = self.is_state_on and isinstance(fan, (int, float)) and fan > 0
        return HVACAction.FAN if running else HVACAction.IDLE

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode != HVACMode.OFF:
            await self.async_write_status({"state": "on"})
            return
        # Turning off while a named automation is active must also drop it
        # back to Manual — otherwise `hvac_mode` above stays locked to
        # FAN_ONLY (custom preset overrides the on/off channel), so an "Off"
        # from HA would silently do nothing visible in the UI. Two spaced-out
        # writes for the same reason as `LarnitechValveHeating`.
        if self._custom_preset_active:
            await self.async_write_status({"automation": ""})
            await asyncio.sleep(_PRESET_RESET_SETTLE)
        await self.async_write_status({"state": "off"})

    @property
    def fan_mode(self) -> str | None:
        fan = self.status.get("fan")
        if isinstance(fan, (int, float)):
            step = min(100, max(0, round(fan / 10) * 10))
            return f"{step}%"
        if self.is_state_on:
            self._warn_once(
                "fan",
                "Larnitech vent %s: missing/non-numeric 'fan' while on (status=%s)",
                self.entity_id,
                self.status,
            )
        return None

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self.async_write_status({"fan": int(fan_mode.rstrip("%"))})

    @property
    def preset_modes(self) -> list[str]:
        modes = list(self.device.get("automations") or [])
        for mode in (MANUAL, ALWAYS_OFF):
            if mode not in modes:
                modes.append(mode)
        return modes

    @property
    def preset_mode(self) -> str:
        raw = self.status.get("automation")
        if raw == ALWAYS_OFF_RAW:
            return ALWAYS_OFF
        return raw or MANUAL

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode == MANUAL:
            value = ""
        elif preset_mode == ALWAYS_OFF:
            value = ALWAYS_OFF_RAW
        else:
            value = preset_mode
        await self.async_write_status({"automation": value})
