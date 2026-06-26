# Updated: 2026-06-26 14:30
"""Constants for the Larnitech integration."""

DOMAIN = "larnitech"

# Remove an HA device only after it is absent from this many full snapshots
# in a row (guards against partial / corrupted get-devices replies).
MISSING_SNAPSHOTS_BEFORE_REMOVE = 2

CONN_LOCAL = "local"
CONN_CLOUD = "cloud"

# Connection (stored in entry.data, edited via Reconfigure flow)
CONF_CONNECTION_TYPE = "connection_type"
CONF_SERIAL = "serial"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_KEY = "key"

# Behaviour toggles (stored in entry.options, edited via Options flow).
CONF_AUTO_REMOVE = "auto_remove"
CONF_UPDATE_NAMES = "update_names"
CONF_USE_AREAS = "use_areas"
CONF_UPDATE_AREAS = "update_areas"

# All toggles default ON.
TOGGLE_DEFAULTS = {
    CONF_AUTO_REMOVE: True,
    CONF_UPDATE_NAMES: True,
    CONF_USE_AREAS: True,
    CONF_UPDATE_AREAS: True,
}

DEFAULT_LOCAL_PORT = 2041
# Push (decoded events) carries real-time updates; this poll is a safety net
# against missed events and doubles as keepalive / dead-connection detection.
DEFAULT_SCAN_INTERVAL = 120

PLATFORMS = [
    "sensor",
    "binary_sensor",
    "event",
    "light",
    "switch",
    "lock",
    "fan",
    "valve",
    "humidifier",
    "cover",
    "climate",
    "button",
]

# `lamp` sub-type -> HA platform. Absent sub-type = plain light.
LAMP_PLATFORM = {
    None: "light",
    "socket": "switch",
    "pump": "switch",
    "closing-switch": "switch",
    "lock": "lock",
    "air-fan": "fan",
    "valve-3": "valve",
    "damper": "valve",
    "dehumidifier": "humidifier",
}


def lamp_platform(device: dict) -> str | None:
    """HA platform for a `lamp` device by sub-type; None = unknown, skip."""
    return LAMP_PLATFORM.get(device.get("sub-type") or None)


# Device types this integration maps to a platform (lamp dispatched by sub-type).
HANDLED_TYPES = {
    "temperature-sensor",
    "humidity-sensor",
    "co2-sensor",
    "illumination-sensor",
    "current-sensor",
    "motion-sensor",
    "door-sensor",
    "leak-sensor",
    "switch",
    "lamp",
    "dimmer-lamp",
    "rgb-lamp",
    "blinds",
    "jalousie",
    "gate",
    "valve-heating",
    "climate-control",
    "AC",
    "conditioner",
    "fancoil",
}


def unhandled_reason(device: dict) -> str | None:
    """Why a device maps to no platform (for diagnostics), or None if handled."""
    dtype = device.get("type")
    if dtype not in HANDLED_TYPES:
        return f"unknown type {dtype!r}"
    if dtype == "lamp" and lamp_platform(device) is None:
        return f"lamp with unknown sub-type {device.get('sub-type')!r}"
    return None


def device_slug(serial, addr: str) -> str:
    """Stable id for a device: <serial>_<ID>_<SUBID> from addr `ID:SUBID`."""
    dev_id, sub_id = addr.split(":")
    return f"{serial or 'local'}_{dev_id}_{sub_id}"


def toggle(entry, key: str) -> bool:
    """Read a behaviour toggle: options override data, default ON."""
    if key in entry.options:
        return entry.options[key]
    return entry.data.get(key, TOGGLE_DEFAULTS[key])
