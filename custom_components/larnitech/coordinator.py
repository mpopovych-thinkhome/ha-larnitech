# Updated: 2026-08-17 16:00
"""Data coordinator: decoded events applied directly; full get-devices as safety.

The full snapshot also drives reconciliation: add/remove devices, react to a
device changing type, and sync names / rooms per the entry's toggles."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import LarnitechClient, LarnitechError
from .const import (
    CONF_AUTO_REMOVE,
    CONF_READ_ONLY,
    CONF_UPDATE_AREAS,
    CONF_UPDATE_NAMES,
    CONF_USE_AREAS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MISSING_SNAPSHOTS_BEFORE_REMOVE,
    device_slug,
    toggle,
    unhandled_reason,
)

_LOGGER = logging.getLogger(__name__)

# Debounce for the rare full refresh (reconnect / unknown-addr fallback).
_REFRESH_COOLDOWN = 2.0

# Climate setpoints are a group that must be REPLACED, not merged. Larnitech
# only sends the setpoints the active automation actually uses (a
# cooling-only zone sends `setpoint-cool` and no `setpoint-heat` at all), so
# a plain merge keeps a setpoint that no longer exists — HA went on showing a
# heat setpoint for a zone switched to cooling only. Any status update
# carrying at least one of these keys defines the complete current set;
# the others are dropped.
_SETPOINT_KEYS = ("setpoint", "setpoint-heat", "setpoint-cool")


def merge_status(old: dict, new: dict) -> dict:
    """Merge a status update, replacing the setpoint group wholesale."""
    merged = {**old, **new}
    if any(key in new for key in _SETPOINT_KEYS):
        for key in _SETPOINT_KEYS:
            if key not in new:
                merged.pop(key, None)
    return merged


class LarnitechCoordinator(DataUpdateCoordinator):
    """Holds the device registry keyed by addr; reconciles HA on full snapshots."""

    def __init__(self, hass, client: LarnitechClient, entry):
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=_REFRESH_COOLDOWN, immediate=True
            ),
        )
        self.client = client
        self.entry = entry
        self._missing: dict[str, int] = {}
        # (type, sub-type) pairs already reported as unmapped — log each once.
        self._logged_unmapped: set = set()

        # Resolved toggles (options override data, default ON).
        self.auto_remove = toggle(entry, CONF_AUTO_REMOVE)
        self.update_names = toggle(entry, CONF_UPDATE_NAMES)
        self.use_areas = toggle(entry, CONF_USE_AREAS)
        self.update_areas = toggle(entry, CONF_UPDATE_AREAS)
        self.read_only = toggle(entry, CONF_READ_ONLY)

    async def _async_update_data(self) -> dict[str, dict]:
        try:
            devices = await self.client.async_get_devices()
        except LarnitechError as err:
            raise UpdateFailed(str(err)) from err
        data = {d["addr"]: d for d in devices if "addr" in d}
        # An empty snapshot is never a real "the controller has no devices" —
        # `async_get_devices` returns [] for a malformed/`devices`-less reply
        # just as it would for a genuinely empty one. Treating it as truth
        # feeds `_reconcile` an all-missing snapshot, and two of those in a
        # row make `auto_remove` delete EVERY device from the HA registry
        # (taking their entities, and any dashboard/automation references to
        # them, with it). Fail the update instead: entities go unavailable
        # and recover on the next good poll.
        if not data and self.data:
            raise UpdateFailed("get-devices returned an empty snapshot — ignoring")
        self._log_unmapped(data)
        self._reconcile(data)
        return data

    @callback
    def _log_unmapped(self, data: dict[str, dict]) -> None:
        """Surface devices that map to no platform, with the raw payload, so an
        unknown type or sub-type can be added to the mapping later."""
        for addr, dev in data.items():
            reason = unhandled_reason(dev)
            if reason is None:
                continue
            key = (dev.get("type"), dev.get("sub-type"))
            if key in self._logged_unmapped:
                continue
            self._logged_unmapped.add(key)
            _LOGGER.warning(
                "Larnitech device not mapped to a platform (%s) at %s — raw: %s",
                reason,
                addr,
                dev,
            )

    @callback
    def apply_events(self, devices: list[dict]) -> None:
        """Merge decoded push events into state without a full re-read.

        Events are partial (only changed keys) and never used for add/remove —
        a missing addr in an event does not mean the device is gone. A single
        device in the batch with a hex status or unknown addr triggers a full
        refresh for itself, but must not discard the other devices in the
        same batch — those are applied immediately regardless."""
        if self.data is None:
            return

        updated = dict(self.data)
        changed = False
        need_full = False
        verify: list[str] = []
        for dev in devices:
            addr = dev.get("addr")
            status = dev.get("status")
            if addr is None:
                continue
            if not isinstance(status, dict):
                _LOGGER.debug(
                    "apply_events: non-dict status for %s: %r — full refresh", addr, status
                )
                need_full = True
                continue
            if addr not in updated:
                _LOGGER.debug(
                    "apply_events: event for unknown addr %s: %r — full refresh", addr, dev
                )
                need_full = True
                continue
            merged = dict(updated[addr])
            merged["status"] = merge_status(merged.get("status", {}), status)
            updated[addr] = merged
            changed = True
            # A preset switch can change which setpoints exist at all — and an
            # event only carries what changed, so "no setpoint key" here is
            # ambiguous (unchanged, or gone with the old automation?). Re-read
            # the device for the unambiguous full status.
            if "automation" in status:
                verify.append(addr)

        if changed:
            self.async_set_updated_data(updated)
        if need_full:
            self.hass.async_create_task(self.async_request_refresh())
        for addr in verify:
            self.hass.async_create_task(self.async_refresh_addr(addr))

    async def async_refresh_addr(self, addr: str) -> None:
        """Point-verify one device after a write — the controller doesn't
        reliably push a `statuses` event for every write (observed live:
        sometimes silent, especially for masked-capability climate writes),
        so the write path calls this instead of trusting `status-set`'s
        `success` blindly. Cheaper than a full `get-devices` refresh and,
        called by the caller after a short delay, avoids racing the
        controller's own internal state settling (an immediate re-read can
        land the pre-write value back into HA).

        `status-get`'s response is a thin `{addr, type, status}` shape — no
        `name`/`area`/`modes`/`vane-hor`/`automations`/... (confirmed live
        2026-08-17), so only the `status` key is taken from it: replacing the
        whole device dict wiped those device-level keys on every write and
        silently broke anything that reads them (e.g. AC's
        `swing_horizontal_modes`, whose default is "none" — losing the mask
        made the whole feature disappear post-write).

        `status` itself IS complete here, unlike a push event — so it
        REPLACES the stored status rather than merging into it. A key absent
        from a full status means the device genuinely doesn't have it, and
        merging would resurrect it: a zone whose automation has no setpoint
        at all sends no setpoint key, and HA went on showing the setpoint
        from the previous automation."""
        if self.data is None or addr not in self.data:
            return
        try:
            device = await self.client.async_get_device_status(addr)
        except LarnitechError as err:
            _LOGGER.debug("Larnitech: post-write refresh of %s failed: %s", addr, err)
            return
        if device is None or not isinstance(device.get("status"), dict):
            return
        updated = dict(self.data)
        merged = dict(updated[addr])
        merged["status"] = device["status"]
        updated[addr] = merged
        self.async_set_updated_data(updated)

    # --- reconciliation (full snapshot only) -----------------------------

    @callback
    def _reconcile(self, data: dict[str, dict]) -> None:
        dev_reg = dr.async_get(self.hass)
        serial = self.client.serial
        snapshot = {device_slug(serial, addr): dev for addr, dev in data.items()}

        for device in dr.async_entries_for_config_entry(dev_reg, self.entry.entry_id):
            slug = next((i[1] for i in device.identifiers if i[0] == DOMAIN), None)
            if slug is None:
                continue
            dev = snapshot.get(slug)

            if dev is None:
                self._handle_missing(dev_reg, device, slug)
                continue
            self._missing.pop(slug, None)

            # Type changed (e.g. lamp -> thermostat): drop so the right platform
            # recreates it. device.model holds the type at creation time.
            new_type = dev.get("type")
            if self.auto_remove and device.model and new_type and device.model != new_type:
                dev_reg.async_remove_device(device.id)
                continue

            self._sync_name(dev_reg, device, dev)
            self._sync_area(dev_reg, device, dev)

    @callback
    def _handle_missing(self, dev_reg, device, slug: str) -> None:
        if not self.auto_remove:
            return
        self._missing[slug] = self._missing.get(slug, 0) + 1
        if self._missing[slug] >= MISSING_SNAPSHOTS_BEFORE_REMOVE:
            # Removing a device takes its entities with it — and with them any
            # dashboard card or automation referencing them. Never do it
            # silently.
            _LOGGER.warning(
                "Larnitech: removing device %s (%s) — absent from %s consecutive "
                "snapshots. Disable the 'auto_remove' option to keep devices "
                "that the controller stops reporting.",
                slug,
                device.name,
                MISSING_SNAPSHOTS_BEFORE_REMOVE,
            )
            dev_reg.async_remove_device(device.id)
            self._missing.pop(slug, None)

    @callback
    def _sync_name(self, dev_reg, device, dev: dict) -> None:
        # Auto-update respects a manual rename; the resync button overrides it.
        if not self.update_names or device.name_by_user is not None:
            return
        name = dev.get("name")
        if name and device.name != name:
            dev_reg.async_update_device(device.id, name=name)

    @callback
    def _sync_area(self, dev_reg, device, dev: dict) -> None:
        if not (self.use_areas and self.update_areas):
            return
        lt_area = dev.get("area")
        if not lt_area:
            return
        area = ar.async_get(self.hass).async_get_or_create(lt_area)
        if device.area_id != area.id:
            dev_reg.async_update_device(device.id, area_id=area.id)

    # --- on-demand resync from the button --------------------------------

    @callback
    def resync_names(self) -> None:
        """Force HA names back to Larnitech, overriding manual renames."""
        dev_reg = dr.async_get(self.hass)
        ent_reg = er.async_get(self.hass)
        serial = self.client.serial
        for addr, dev in (self.data or {}).items():
            name = dev.get("name")
            if not name:
                continue
            device = dev_reg.async_get_device(
                identifiers={(DOMAIN, device_slug(serial, addr))}
            )
            if device is None:
                continue
            dev_reg.async_update_device(device.id, name=name, name_by_user=None)
            for ent in er.async_entries_for_device(
                ent_reg, device.id, include_disabled_entities=True
            ):
                if ent.name is not None:
                    ent_reg.async_update_entity(ent.entity_id, name=None)
