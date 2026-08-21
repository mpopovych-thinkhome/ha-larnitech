# Updated: 2026-08-21 11:04
"""Larnitech measurement sensors (read-only), added/removed dynamically."""
from __future__ import annotations

from homeassistant.components.sensor import (
    ENTITY_ID_FORMAT,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    CONCENTRATION_PARTS_PER_MILLION,
    LIGHT_LUX,
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import callback

from .const import DOMAIN, json_platform, virtual_platform
from .entity import LarnitechEntity

# `virtual` sub-types that carry their value in `status.state` (numeric or
# text) with no fixed device_class/unit — confirmed live 2026-08-20.
# `prf` deliberately excluded: confirmed live it carries `hex`, not `state`
# (see const.py VIRTUAL_PLATFORM).
VIRTUAL_SENSOR_SUBTYPES = {"sensor", "text", "long-text"}

# `virtual` sub-type -> icon.
VIRTUAL_ICON = {
    "sensor": "mdi:numeric",
    "text": "mdi:format-text",
    "long-text": "mdi:card-text",
}

# Larnitech type -> (device_class, unit). All read a numeric `status.state`.
SENSORS = {
    "temperature-sensor": (SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS),
    "humidity-sensor": (SensorDeviceClass.HUMIDITY, PERCENTAGE),
    "co2-sensor": (SensorDeviceClass.CO2, CONCENTRATION_PARTS_PER_MILLION),
    "illumination-sensor": (SensorDeviceClass.ILLUMINANCE, LIGHT_LUX),
    "current-sensor": (SensorDeviceClass.CURRENT, UnitOfElectricCurrent.AMPERE),
}

# Larnitech type -> forced display precision (decimal digits). Unlisted types
# use HA's own default precision.
DISPLAY_PRECISION = {
    "illumination-sensor": 1,
}

# HA rejects (and noisily logs) any state string past this length.
MAX_STATE_LENGTH = 255

# Larnitech type -> status key for a companion diagnostic sensor that rides
# the same device as another platform's primary entity for that addr (e.g.
# `climate-control`'s own entity lives in climate.py) — for a status field
# that primary entity's own domain has no slot for.
DIAGNOSTIC_SENSORS = {
    "climate-control": "pid-temperature",
}

# `json` field `descr.dim` -> (device_class, unit, state_class). Confirmed
# live 2026-08-21 on MBUS heat/water meters. Energy/Volume are cumulative
# meter registers -> TOTAL_INCREASING; everything else is an instantaneous
# reading -> MEASUREMENT. A field whose `dim` isn't in this map (or has none
# at all — serial numbers, raw timestamps, bitmasks) gets no unit/device_class
# and is filed as diagnostic-only (see `LarnitechJsonFieldSensor`).
JSON_DIM_MAP = {
    "Wh": (SensorDeviceClass.ENERGY, UnitOfEnergy.WATT_HOUR, SensorStateClass.TOTAL_INCREASING),
    "m3": (SensorDeviceClass.VOLUME, UnitOfVolume.CUBIC_METERS, SensorStateClass.TOTAL_INCREASING),
    "W": (SensorDeviceClass.POWER, UnitOfPower.WATT, SensorStateClass.MEASUREMENT),
    "C": (SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, SensorStateClass.MEASUREMENT),
    # `dim: "K"` is a temperature *difference* (e.g. flow-return delta) — shown
    # in Kelvin as Larnitech itself reports it (user call 2026-08-21), even
    # though numerically a 1K step equals 1°C (only the zero offset differs,
    # and it cancels out in a difference).
    "K": (SensorDeviceClass.TEMPERATURE, UnitOfTemperature.KELVIN, SensorStateClass.MEASUREMENT),
    "m3/h": (SensorDeviceClass.VOLUME_FLOW_RATE, UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR, SensorStateClass.MEASUREMENT),
    "seconds": (SensorDeviceClass.DURATION, UnitOfTime.SECONDS, SensorStateClass.MEASUREMENT),
    "days": (SensorDeviceClass.DURATION, UnitOfTime.DAYS, SensorStateClass.MEASUREMENT),
}


def _json_field_keys(device: dict) -> set[str]:
    """Numeric field keys in a `json` device's raw payload — everything in
    `_raw` except the `descr`/`hr` bookkeeping keys."""
    raw = device.get("status", {}).get("_raw") or {}
    return {k for k in raw if isinstance(k, str) and k.isdigit()}


# `json` field `descr.typ` values that get "N years, M months" formatting
# instead of a raw seconds/days number (user call 2026-08-21) — cumulative
# runtime counters, not something anyone reads as a raw duration.
JSON_DURATION_TYPES = {"On Time", "Operating Time"}
_DAYS_PER_MONTH = 30.4375  # average Gregorian month (365.25 / 12)


def _format_years_months(total_days: float) -> str:
    total_months = total_days / _DAYS_PER_MONTH
    years, months = divmod(int(total_months), 12)
    return f"{years} years, {months} months"


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[tuple[str, str]] = set()

    @callback
    def _add_new():
        current = {(a, "measurement") for a, d in coordinator.data.items() if d.get("type") in SENSORS}
        current |= {
            (a, "pid") for a, d in coordinator.data.items() if d.get("type") in DIAGNOSTIC_SENSORS
        }
        current |= {
            (a, "virtual")
            for a, d in coordinator.data.items()
            if d.get("type") == "virtual" and d.get("sub-type") in VIRTUAL_SENSOR_SUBTYPES
        }
        current |= {
            (a, f"json:{key}")
            for a, d in coordinator.data.items()
            if d.get("type") == "json" and json_platform(d) == "sensor"
            for key in _json_field_keys(d)
        }
        new = []
        for addr, kind in current - known:
            if kind == "measurement":
                dtype = coordinator.data[addr]["type"]
                device_class, unit = SENSORS[dtype]
                new.append(
                    LarnitechSensor(
                        coordinator, addr, device_class, unit, DISPLAY_PRECISION.get(dtype)
                    )
                )
            elif kind == "pid":
                status_key = DIAGNOSTIC_SENSORS[coordinator.data[addr]["type"]]
                new.append(LarnitechPidSensor(coordinator, addr, status_key))
            elif kind.startswith("json:"):
                new.append(LarnitechJsonFieldSensor(coordinator, addr, kind.split(":", 1)[1]))
            else:
                new.append(LarnitechVirtualSensor(coordinator, addr))
        known.clear()
        known.update(current)
        if new:
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_listener(_add_new))
    _add_new()


class LarnitechSensor(LarnitechEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, addr, device_class, unit, display_precision=None):
        super().__init__(coordinator, addr)
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        if display_precision is not None:
            self._attr_suggested_display_precision = display_precision
        self.entity_id = ENTITY_ID_FORMAT.format(self._slug)

    @property
    def native_value(self):
        value = self.status.get("state")
        if isinstance(value, (int, float)):
            return value
        if value is not None:
            self._warn_once(
                f"state:{value!r}",
                "Larnitech sensor %s: non-numeric state %r (status=%s)",
                self.entity_id,
                value,
                self.status,
            )
        return None


class LarnitechPidSensor(LarnitechEntity, SensorEntity):
    """Companion diagnostic sensor for a climate device's PID heat/cool
    demand — signed percent, positive = heating, negative = cooling (sign
    not yet confirmed live — see climate.py `LarnitechClimateControl.hvac_action`).
    The `climate` domain has no numeric-percent slot for this, so it rides
    alongside the climate entity on the same device (shared `addr` -> same
    DeviceInfo, distinct unique_id)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:sine-wave"

    def __init__(self, coordinator, addr, status_key: str):
        super().__init__(coordinator, addr)
        self._status_key = status_key
        self._attr_unique_id = f"{self._slug}_pid"
        self.entity_id = ENTITY_ID_FORMAT.format(self._attr_unique_id)

    @property
    def name(self) -> str:
        return f"{super().name} PID demand"

    @property
    def native_value(self):
        value = self.status.get(self._status_key)
        return value if isinstance(value, (int, float)) else None


class LarnitechVirtualSensor(LarnitechEntity, SensorEntity):
    """Generic `virtual` sensor/text/long-text — no fixed device_class or
    unit, value is whatever `status.state` carries (number or string).

    `long-text` widgets legitimately carry multi-line blobs far past HA's
    255-character state ceiling (a Komfovent status dump runs several
    hundred). Handing one to HA raw makes it reject the state, drop the
    entity to `unknown`, and log the whole blob at ERROR — on every single
    update. On one object that alone pinned a CPU core at ~55% (found live
    2026-08-20). The state is truncated to fit; every text widget (`text`/
    `long-text`, any length) also carries the untruncated value in the
    `full_text` attribute (HA doesn't cap attribute length), so automations
    have one consistent place to read the whole thing regardless of size.

    `sensor` sub-type is numeric — unlike `text`/`long-text` it gets
    `state_class=MEASUREMENT` so HA records long-term statistics and chart
    cards (history-graph, statistics-graph, trend-graph) work on it. That
    also means HA rejects any non-numeric state on it with a ValueError on
    every update — and a numeric widget can legitimately report the string
    `"undefined"` when its script hasn't produced a value yet (confirmed
    live 2026-08-21 on `902:245` "Vent:Filter"), so a non-numeric value is
    reported as unknown rather than passed through."""

    def __init__(self, coordinator, addr):
        super().__init__(coordinator, addr)
        self.entity_id = ENTITY_ID_FORMAT.format(self._slug)

    @property
    def _is_numeric(self) -> bool:
        return self.device.get("sub-type") == "sensor"

    @property
    def icon(self) -> str | None:
        return VIRTUAL_ICON.get(self.device.get("sub-type"))

    @property
    def state_class(self) -> SensorStateClass | None:
        return SensorStateClass.MEASUREMENT if self._is_numeric else None

    @property
    def native_value(self):
        value = self.status.get("state")
        if self._is_numeric:
            return value if isinstance(value, (int, float)) else None
        if isinstance(value, str) and len(value) > MAX_STATE_LENGTH:
            return value[:MAX_STATE_LENGTH]
        return value

    @property
    def extra_state_attributes(self) -> dict | None:
        if self._is_numeric:
            return None
        value = self.status.get("state")
        return {"full_text": value} if isinstance(value, str) else None


class LarnitechJsonFieldSensor(LarnitechEntity, SensorEntity):
    """One field of a `json` aggregate (MBUS meter, etc.) — unlike every
    other Larnitech type, `json` has no fixed field list: the widget's own
    `descr` dict self-describes each numeric field (`typ` name, optional
    `dim` unit, optional `func`), so entities are generated dynamically per
    field instead of from a static dict. A field with a `dim` in
    `JSON_DIM_MAP` becomes a real measurement; anything else (serial
    numbers, raw timestamps, bitmasks, an unmapped unit) is diagnostic-only
    — still visible, just without a unit/device_class implying a meaning
    that isn't confirmed. `hr` (a compact model/serial identifier, e.g.
    `3080310:AXI:11:Heat / Cooling load meter:37:16:0`) rides as the
    device's `hw_version`, not its own entity. `On Time`/`Operating Time`
    fields (runtime counters in seconds or days) render as "N years,
    M months" instead of a raw number (user call 2026-08-21) — the raw
    value is kept in the `raw_seconds`/`raw_days` attribute.

    `descr` is sometimes present but `null` (confirmed live 2026-08-21 on
    the Slovackio MBUS meters — a meter that misses a poll returns every
    field, `descr` and `hr` included, as `null` for that whole cycle;
    which meter goes quiet rotates) — not just absent, so
    `.get("descr", {})` doesn't cover it. Two defences:

    - the last non-empty `descr` is cached (`_cache_descr`), so a null tick
      doesn't flap the name/unit back to the `Field N` fallback;
    - everything descr-derived (name, icon, device_class, unit, state_class,
      entity_category, duration formatting) is read through that cache on
      every access rather than captured once at creation, so a field created
      during a null tick picks up the real metadata on the first good poll.

    HA still caches `friendly_name` in the entity registry at registration,
    so a field first registered during a null tick keeps showing `Field N`
    for the rest of that HA session even after the cache fills."""

    def __init__(self, coordinator, addr, field_key: str):
        super().__init__(coordinator, addr)
        self._field_key = field_key
        self._attr_unique_id = f"{self._slug}_{field_key}"
        self.entity_id = ENTITY_ID_FORMAT.format(self._attr_unique_id)
        self._descr_cache: dict = {}
        self._cache_descr()

        hr = self._raw.get("hr")
        if hr:
            self._attr_device_info["hw_version"] = hr

    def _cache_descr(self) -> None:
        descr = (self._raw.get("descr") or {}).get(self._field_key)
        if descr:
            self._descr_cache = descr

    @callback
    def _handle_coordinator_update(self) -> None:
        self._cache_descr()
        super()._handle_coordinator_update()

    @property
    def _raw(self) -> dict:
        return self.status.get("_raw") or {}

    @property
    def _descr(self) -> dict:
        return self._descr_cache

    @property
    def _typ(self) -> str:
        return self._descr.get("typ") or f"Field {self._field_key}"

    @property
    def _dim(self) -> str | None:
        return self._descr.get("dim")

    @property
    def _duration_dim(self) -> str | None:
        dim = self._dim
        return dim if self._typ in JSON_DURATION_TYPES and dim in ("seconds", "days") else None

    @property
    def name(self) -> str:
        func = self._descr.get("func")
        suffix = f"{self._typ} ({func})" if func else self._typ
        return f"{super().name} {suffix}"

    @property
    def icon(self) -> str | None:
        return "mdi:camera-timer" if self._duration_dim else None

    @property
    def device_class(self) -> SensorDeviceClass | None:
        if self._duration_dim:
            return None
        return JSON_DIM_MAP.get(self._dim, (None, None, None))[0]

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self._duration_dim:
            return None
        return JSON_DIM_MAP.get(self._dim, (None, None, None))[1]

    @property
    def state_class(self) -> SensorStateClass | None:
        if self._duration_dim:
            return None
        return JSON_DIM_MAP.get(self._dim, (None, None, None))[2]

    @property
    def entity_category(self) -> EntityCategory | None:
        return EntityCategory.DIAGNOSTIC if self._dim is None else None

    @property
    def native_value(self):
        value = self._raw.get(self._field_key)
        if not isinstance(value, (int, float)):
            return None
        duration_dim = self._duration_dim
        if duration_dim:
            days = value / 86400 if duration_dim == "seconds" else value
            return _format_years_months(days)
        return value

    @property
    def extra_state_attributes(self) -> dict | None:
        duration_dim = self._duration_dim
        if not duration_dim:
            return None
        value = self._raw.get(self._field_key)
        if not isinstance(value, (int, float)):
            return None
        return {f"raw_{duration_dim}": value}
