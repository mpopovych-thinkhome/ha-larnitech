# Updated: 2026-08-21 18:05
"""Shared base entity for Larnitech."""
from __future__ import annotations

import asyncio
import logging

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, device_display_name, device_slug, entity_object_id, hub_slug

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
        self._object_id = entity_object_id(
            coordinator.entity_id_pattern, coordinator.client.serial, addr, self.device
        )
        self._initial_name = self.device.get("name") or addr
        self._initial_kind = self.device.get("sub-type") or self.device.get("type") or addr
        self._warned: set[str] = set()
        # Dump the raw shape so unexpected payloads are diagnosable. INFO once
        # the platforms' first pass is done — an entity created after that
        # means a device appeared at runtime, and that is worth seeing without
        # debug logging. DEBUG during setup, where every entity would log.
        _LOGGER.log(
            logging.INFO if coordinator.setup_complete else logging.DEBUG,
            "Larnitech: creating entity %s for %s (type=%s sub-type=%s status=%s)",
            type(self).__name__,
            self._slug,
            self.device.get("type"),
            self.device.get("sub-type"),
            self.status,
        )

        # `model` fills HA's device-card subtitle ("<model> • <area> • <N>
        # entities") on the integration page — "type (sub-type)", or plain
        # "type" when there is no sub-type. The addr lives in the NAME
        # instead (see `device_display_name`): that subtitle is not rendered
        # anywhere else, so the addr has to be in the name to stay visible in
        # search / the hub's "Connected devices" list.
        #
        # `sw_version` persists the sub-type across restarts the same way
        # `model` persists the type (coordinator.py's `_reconcile` parses the
        # type back out of `model`) — a live change is caught the same way,
        # from the very next poll after this entity was created.
        dtype = self.device.get("type") or "?"
        dsub = self.device.get("sub-type")
        device_info = DeviceInfo(
            identifiers={(DOMAIN, self._slug)},
            name=device_display_name(self.device, self._initial_name),
            manufacturer="Larnitech",
            model=f"{dtype} ({dsub})" if dsub else dtype,
            sw_version=dsub,
            via_device=(DOMAIN, hub_slug(coordinator.client.serial)),
        )
        if coordinator.use_areas and self.device.get("area"):
            device_info["suggested_area"] = self.device["area"]
        self._attr_device_info = device_info

    def _oid(self, suffix: str | None = None) -> str:
        """entity_id object_id, optionally for a companion entity on the same
        addr (`..._pid`, `..._malfunction`, one per `json` field)."""
        return f"{self._object_id}_{suffix}" if suffix else self._object_id

    @property
    def larnitech_name(self) -> str:
        """The widget's kind — sub-type when Larnitech reports one, else the
        type (e.g. `sensor`, `climate-control`) — without the addr suffix.
        Identity (which physical widget this is) lives on the DEVICE, named
        "ID:SUBID: <Larnitech name>" — the entity's own default name only
        says what it represents, not which one it is. Subclasses that build a
        compound name start from this, then hand the result to `_with_addr`
        so the suffix stays at the very end."""
        # Follow Larnitech live when auto-update is on; otherwise keep the
        # kind captured at creation (user is free to rename in HA).
        if self.coordinator.update_names:
            return self.device.get("sub-type") or self.device.get("type") or self._initial_kind
        return self._initial_kind

    def _with_addr(self, name: str) -> str:
        """Append the Larnitech addr when the option is on. Only entity names
        carry it — a device already shows its addr in the `model` field of
        HA's device-card subtitle. Purely the entity's default name: a manual
        rename lives in the entity registry and wins over this either way, so
        toggling the option never overwrites one."""
        return f"{name} ({self._addr})" if self.coordinator.name_suffix_addr else name

    @property
    def name(self) -> str:
        return self._with_addr(self.larnitech_name)

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
