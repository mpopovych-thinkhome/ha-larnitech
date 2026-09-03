# Updated: 2026-09-03 12:45
"""Constants for the Larnitech integration."""

from homeassistant.util import slugify

DOMAIN = "larnitech"

# Remove an HA device only after it is absent from this many full snapshots
# in a row (guards against partial / corrupted get-devices replies).
MISSING_SNAPSHOTS_BEFORE_REMOVE = 2

# Above this fraction of previously-known devices missing from one snapshot,
# the coordinator stops trusting the normal auto-remove debounce (a hiccup
# can persist across it too) and raises a repair issue instead.
MASS_REMOVAL_RATIO = 0.5

CONN_LOCAL = "local"
CONN_CLOUD = "cloud"

# Connection (stored in entry.data, edited via Reconfigure flow)
CONF_CONNECTION_TYPE = "connection_type"
CONF_SERIAL = "serial"
CONF_IP = "host"
CONF_PORT = "port"
CONF_KEY = "key"

# Behaviour toggles (stored in entry.options, edited via Options flow).
CONF_AUTO_REMOVE = "auto_remove"
CONF_UPDATE_NAMES = "update_names"
CONF_USE_AREAS = "use_areas"
CONF_UPDATE_AREAS = "update_areas"
CONF_READ_ONLY = "read_only"
CONF_NAME_SUFFIX_ADDR = "name_suffix_addr"

OPTIONS_KEYS = (
    CONF_AUTO_REMOVE, CONF_UPDATE_NAMES, CONF_USE_AREAS, CONF_UPDATE_AREAS,
    CONF_READ_ONLY, CONF_NAME_SUFFIX_ADDR,
)

# All toggles default ON, except `read_only` and `name_suffix_addr` — opt-in,
# they change write behavior / naming.
TOGGLE_DEFAULTS = {
    CONF_AUTO_REMOVE: True,
    CONF_UPDATE_NAMES: True,
    CONF_USE_AREAS: True,
    CONF_UPDATE_AREAS: True,
    CONF_READ_ONLY: False,
    CONF_NAME_SUFFIX_ADDR: False,
}

DEFAULT_LOCAL_PORT = 2041
# Push (decoded events) carries real-time updates; this poll is a safety net
# against missed events and doubles as keepalive / dead-connection detection.
CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = 120
# Below 30s the poll competes with the push channel for no benefit; the upper
# bound stays under the controller's own ~300s idle timeout so the poll still
# doubles as keepalive.
MIN_SCAN_INTERVAL = 30
MAX_SCAN_INTERVAL = 290


# --- entity_id naming ---------------------------------------------------
#
# Lives in entry.data, set in the initial flow and changeable via Reconfigure
# — never in the Options flow. Changing it renames every entity_id on the
# entry, which HA does NOT propagate to dashboards, automations or scripts;
# that belongs behind the deliberate Reconfigure step, not a settings toggle.
CONF_ENTITY_ID_PATTERN = "entity_id_pattern"

PATTERN_SERIAL_ID_SUBID = "serial_id_subid"
PATTERN_LARNITECH_ID_SUBID = "larnitech_id_subid"
PATTERN_ROOM_NAME = "room_devicename"
PATTERN_ROOM_NAME_ID_SUBID = "room_devicename_id_subid"

ENTITY_ID_PATTERNS = [
    PATTERN_SERIAL_ID_SUBID,
    PATTERN_LARNITECH_ID_SUBID,
    PATTERN_ROOM_NAME,
    PATTERN_ROOM_NAME_ID_SUBID,
]
DEFAULT_ENTITY_ID_PATTERN = PATTERN_SERIAL_ID_SUBID


def entity_object_id(pattern: str, serial, addr: str, device: dict) -> str:
    """object_id part of an entity_id, per the entry's chosen pattern.

    Only the entity_id is affected — `unique_id` always stays `device_slug`,
    so switching patterns between objects never collides in the registry.
    The name-based patterns can collide between two devices sharing a room
    and a name; HA resolves that by appending `_2`, which is why the
    addr-bearing variants exist."""
    dev_id, sub_id = addr.split(":")
    tail = f"{dev_id}_{sub_id}"
    if pattern == PATTERN_LARNITECH_ID_SUBID:
        return f"{DOMAIN}_{tail}"
    if pattern in (PATTERN_ROOM_NAME, PATTERN_ROOM_NAME_ID_SUBID):
        base = slugify(f"{device.get('area') or ''} {device.get('name') or ''}")
        if not base:
            return f"{serial or 'local'}_{tail}"
        return base if pattern == PATTERN_ROOM_NAME else f"{base}_{tail}"
    return f"{serial or 'local'}_{tail}"

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
    "number",
    "media_player",
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


# `virtual` sub-type -> HA platform. `sensor`/`text`/`long-text` carry
# their value in `status.state` (confirmed live 2026-08-20).
#
# Deliberately excluded (confirmed live 2026-08-20/21, not just undecided):
# `jalousie`/`gate`(+`120`) carry no `state` at all, only an undocumented
# `hex` field — the real cover types' open/close model does not apply;
# `prf` also has no `state`, only `hex`; `sunrise`/`plan` are `hex`/`state`
# with no known meaning. `lamp`/`dimer-lamp`/`rgb-lamp` all cancelled, user
# call 2026-08-21 — every live example reported erroneous/unstable status
# (`rgb-lamp` 1:224: `level`/`saturation`/`hue` all `101.6`, past the valid
# 0-100 range). See `larnitech_integration_spec.md` "Virtual" table.
VIRTUAL_PLATFORM = {
    "ventilation": "climate",
    "sensor": "sensor",
    "text": "sensor",
    "long-text": "sensor",
}


def virtual_platform(device: dict) -> str | None:
    """HA platform for a `virtual` device by sub-type; None = unknown, skip."""
    return VIRTUAL_PLATFORM.get(device.get("sub-type"))


# `json` sub-types deliberately NOT dispatched — user call 2026-08-21.
# `btunreg` (`900:1` on the Imerel stand) is a raw, mostly-empty diagnostic
# blob with no useful fields (confirmed live). Any other sub-type (e.g.
# `MBUS` meters, confirmed live on a separate object with real Energy/
# Volume/Temperature fields) IS handled generically — see sensor.py.
JSON_EXCLUDED_SUBTYPES = {"btunreg"}


def json_platform(device: dict) -> str | None:
    """HA platform for a `json` device by sub-type; None = excluded/unknown."""
    if device.get("sub-type") in JSON_EXCLUDED_SUBTYPES:
        return None
    return "sensor"


# Device types this integration maps to a platform (lamp/virtual dispatched by sub-type).
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
    "light-scheme",
    "blinds",
    "jalousie",
    "gate",
    "valve-heating",
    "climate-control",
    "AC",
    "conditioner",
    "fancoil",
    "valve",
    "vent",
    "speaker",
}


def unhandled_reason(device: dict) -> str | None:
    """Why a device maps to no platform (for diagnostics), or None if handled."""
    dtype = device.get("type")
    if dtype == "virtual":
        if virtual_platform(device) is None:
            return f"virtual with unhandled sub-type {device.get('sub-type')!r}"
        return None
    if dtype == "json":
        if json_platform(device) is None:
            return f"json with excluded sub-type {device.get('sub-type')!r}"
        return None
    if dtype not in HANDLED_TYPES:
        return f"unknown type {dtype!r}"
    if dtype == "lamp" and lamp_platform(device) is None:
        return f"lamp with unknown sub-type {device.get('sub-type')!r}"
    return None


def device_slug(serial, addr: str) -> str:
    """Stable id for a device: <serial>_<ID>_<SUBID> from addr `ID:SUBID`."""
    dev_id, sub_id = addr.split(":")
    return f"{serial or 'local'}_{dev_id}_{sub_id}"


def device_display_name(dev: dict, fallback: str | None = None) -> str | None:
    """"ID:SUBID name" — the addr identifies the widget on the controller,
    the name is what Larnitech calls it.

    The addr also rides in `model`, but HA renders `model` only in the
    integration's own device list — the hub device's "Connected devices"
    panel and the global device search show the NAME and nothing else, so
    the addr has to be part of the name to be visible there at all."""
    addr = dev.get("addr")
    name = dev.get("name") or fallback
    if not name:
        return addr
    return f"{addr} {name}" if addr else name


def hub_slug(serial) -> str:
    """Identifier of the controller's own device. Every widget device links to
    it via `via_device`, so HA shows one Larnitech controller with everything
    connected through it instead of a flat list with no owner."""
    return f"{serial or 'local'}_hub"


def toggle(entry, key: str) -> bool:
    """Read a behaviour toggle: options override data, default ON."""
    if key in entry.options:
        return entry.options[key]
    return entry.data.get(key, TOGGLE_DEFAULTS[key])
