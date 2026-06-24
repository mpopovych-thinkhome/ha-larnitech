# Updated: 2026-06-24 15:55
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
    CONF_UPDATE_AREAS,
    CONF_UPDATE_NAMES,
    CONF_USE_AREAS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MISSING_SNAPSHOTS_BEFORE_REMOVE,
    device_slug,
    toggle,
)

_LOGGER = logging.getLogger(__name__)

# Debounce for the rare full refresh (reconnect / unknown-addr fallback).
_REFRESH_COOLDOWN = 2.0


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

        # Resolved toggles (options override data, default ON).
        self.auto_remove = toggle(entry, CONF_AUTO_REMOVE)
        self.update_names = toggle(entry, CONF_UPDATE_NAMES)
        self.use_areas = toggle(entry, CONF_USE_AREAS)
        self.update_areas = toggle(entry, CONF_UPDATE_AREAS)

    async def _async_update_data(self) -> dict[str, dict]:
        try:
            devices = await self.client.async_get_devices()
        except LarnitechError as err:
            raise UpdateFailed(str(err)) from err
        data = {d["addr"]: d for d in devices if "addr" in d}
        self._reconcile(data)
        return data

    @callback
    def apply_events(self, devices: list[dict]) -> None:
        """Merge decoded push events into state without a full re-read.

        Events are partial (only changed keys) and never used for add/remove —
        a missing addr in an event does not mean the device is gone. Falls back
        to a full refresh for hex (string) status or an unknown addr."""
        if self.data is None:
            return

        updated = dict(self.data)
        need_full = False
        for dev in devices:
            addr = dev.get("addr")
            status = dev.get("status")
            if addr is None:
                continue
            if not isinstance(status, dict) or addr not in updated:
                need_full = True
                continue
            merged = dict(updated[addr])
            merged_status = dict(merged.get("status", {}))
            merged_status.update(status)
            merged["status"] = merged_status
            updated[addr] = merged

        if need_full:
            self.hass.async_create_task(self.async_request_refresh())
            return
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
