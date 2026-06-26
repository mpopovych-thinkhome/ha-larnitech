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

PLATFORMS = ["sensor", "binary_sensor", "event", "light", "button"]


def device_slug(serial, addr: str) -> str:
    """Stable id for a device: <serial>_<ID>_<SUBID> from addr `ID:SUBID`."""
    dev_id, sub_id = addr.split(":")
    return f"{serial or 'local'}_{dev_id}_{sub_id}"


def toggle(entry, key: str) -> bool:
    """Read a behaviour toggle: options override data, default ON."""
    if key in entry.options:
        return entry.options[key]
    return entry.data.get(key, TOGGLE_DEFAULTS[key])
