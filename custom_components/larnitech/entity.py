# Updated: 2026-08-17 15:20
"""Shared base entity for Larnitech."""
from __future__ import annotations

import asyncio
import logging

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, device_slug

_LOGGER = logging.getLogger(__name__)

# Delay before re-reading a device after a write. The controller sometimes
# doesn't push a `statuses` event back for a write at all (observed live,
# 2026-08-17 — feedback occasionally never arrives), and an IMMEDIATE re-read
# races the controller's own internal state settling: read too soon and you
# get the pre-write value back, overwriting HA's optimistic state with stale
# data. 1s is a guess, not a measured value — revisit if writes still show
# stale state after this.
_WRITE_VERIFY_DELAY = 1


class LarnitechEntity(CoordinatorEntity):
    """Base: stable unique_id = <serial>_<id>_<subid>."""

    _attr_has_entity_name = False

    def __init__(self, coordinator, addr: str):
        super().__init__(coordinator)
        self._addr = addr
        self._slug = device_slug(coordinator.client.serial, addr)
        self._attr_unique_id = self._slug
        self._initial_name = self.device.get("name") or addr
        self._warned: set[str] = set()
        # Dump the raw shape once so unexpected payloads are diagnosable.
        _LOGGER.debug(
            "Larnitech entity %s: type=%s sub-type=%s status=%s",
            self._slug,
            self.device.get("type"),
            self.device.get("sub-type"),
            self.status,
        )

        # `model` is the only free-text slot in HA's device-card subtitle
        # ("<model> • <area> • <N> entities") — pack the Larnitech addr into
        # it so devices are findable/identifiable by addr in that list.
        dtype = self.device.get("type") or "?"
        device_info = DeviceInfo(
            identifiers={(DOMAIN, self._slug)},
            name=self._initial_name,
            manufacturer="Larnitech",
            model=f"{dtype}, {addr}",
        )
        if coordinator.use_areas and self.device.get("area"):
            device_info["suggested_area"] = self.device["area"]
        self._attr_device_info = device_info

    @property
    def name(self) -> str:
        # Follow Larnitech live when auto-update is on; otherwise keep the
        # name captured at creation (user is free to rename in HA).
        if self.coordinator.update_names:
            return self.device.get("name") or self._initial_name
        return self._initial_name

    @property
    def device(self) -> dict:
        return self.coordinator.data.get(self._addr, {})

    @property
    def status(self) -> dict:
        return self.device.get("status", {})

    @property
    def available(self) -> bool:
        return super().available and self._addr in self.coordinator.data

    def _warn_once(self, key: str, msg: str, *args) -> None:
        """Log a warning the first time a given anomaly key is seen (no per-poll spam)."""
        if key in self._warned:
            return
        self._warned.add(key)
        _LOGGER.warning(msg, *args)

    # --- on/off write shared by lamp and its actuator sub-types ----------

    @property
    def is_state_on(self) -> bool:
        return self.status.get("state") == "on"

    async def async_write_status(self, status: dict) -> None:
        if not self.coordinator.read_only:
            await self.coordinator.client.async_set_status(self._addr, status)
        # Fire-and-forget: verifying is a courtesy, not part of the write
        # itself — don't make the HA service call (and the UI spinner) wait
        # out the delay. See `_WRITE_VERIFY_DELAY` for why the delay exists.
        # In `read_only` mode there is no actual write above, but a control
        # action from HA still ends the same way after the same delay: the
        # entity is re-read and snaps back to Larnitech's real status.
        self.hass.async_create_task(self._async_verify_write())

    async def _async_verify_write(self) -> None:
        await asyncio.sleep(_WRITE_VERIFY_DELAY)
        await self.coordinator.async_refresh_addr(self._addr)

    async def async_set_state(self, on: bool) -> None:
        await self.async_write_status({"state": "on" if on else "off"})
