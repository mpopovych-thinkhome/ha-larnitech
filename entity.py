# Updated: 2026-06-24 15:55
"""Shared base entity for Larnitech."""
from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, device_slug


class LarnitechEntity(CoordinatorEntity):
    """Base: stable unique_id = <serial>_<id>_<subid>."""

    _attr_has_entity_name = False

    def __init__(self, coordinator, addr: str):
        super().__init__(coordinator)
        self._addr = addr
        self._slug = device_slug(coordinator.client.serial, addr)
        self._attr_unique_id = self._slug
        self._initial_name = self.device.get("name") or addr

        device_info = DeviceInfo(
            identifiers={(DOMAIN, self._slug)},
            name=self._initial_name,
            manufacturer="Larnitech",
            model=self.device.get("type"),
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
