# Updated: 2026-08-21 17:05
"""Diagnostics download for a Larnitech entry (Settings -> the entry's
three-dot menu -> "Download diagnostics").

The point is that a bug report carries the controller's own raw
`get-devices` payload, not a description of it: almost every quirk found so
far (fields arriving `null`, a mask never sent, an undocumented sub-type)
was only diagnosable from the raw shape."""
from __future__ import annotations

from collections import Counter

from homeassistant.components.diagnostics import async_redact_data

from .const import CONF_KEY, DOMAIN, unhandled_reason

# The API key is the only credential here. The serial is kept: it is the
# object identifier every log line and entity_id is keyed by, and it is
# useless to an attacker without the key.
TO_REDACT = {CONF_KEY}


def _summary(data: dict) -> dict:
    """Type/sub-type census plus everything the mapping skipped — the two
    questions asked of every payload before anything else."""
    types = Counter(
        f"{d.get('type')}/{d.get('sub-type')}" if d.get("sub-type") else str(d.get("type"))
        for d in data.values()
    )
    unmapped = {
        addr: reason
        for addr, d in data.items()
        if (reason := unhandled_reason(d)) is not None
    }
    return {
        "device_count": len(data),
        "types": dict(sorted(types.items())),
        "unmapped": unmapped,
    }


async def async_get_config_entry_diagnostics(hass, entry) -> dict:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data or {}
    return {
        "entry": {
            "data": async_redact_data(entry.data, TO_REDACT),
            "options": dict(entry.options),
        },
        "coordinator": {
            "serial": coordinator.client.serial,
            "scan_interval": coordinator.scan_interval,
            "entity_id_pattern": coordinator.entity_id_pattern,
            "last_update_success": coordinator.last_update_success,
            "last_exception": str(coordinator.last_exception) if coordinator.last_exception else None,
            "missing_devices": dict(coordinator._missing),
        },
        "summary": _summary(data),
        "devices": data,
    }


async def async_get_device_diagnostics(hass, entry, device) -> dict:
    """Same payload narrowed to one device — the raw entry for its addr."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    slug = next((i[1] for i in device.identifiers if i[0] == DOMAIN), None)
    raw = {
        addr: d
        for addr, d in (coordinator.data or {}).items()
        if f"{coordinator.client.serial or 'local'}_{addr.replace(':', '_')}" == slug
    }
    return {
        "entry": {"data": async_redact_data(entry.data, TO_REDACT)},
        "device": {"slug": slug, "name": device.name, "model": device.model},
        "raw": raw,
    }
