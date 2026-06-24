# Updated: 2026-06-24 15:55
"""Larnitech 'resync names' button: pull names from Larnitech and apply to HA."""
from __future__ import annotations

from homeassistant.components.button import ENTITY_ID_FORMAT, ButtonEntity
from homeassistant.const import EntityCategory

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([LarnitechResyncNamesButton(coordinator)])


class LarnitechResyncNamesButton(ButtonEntity):
    _attr_has_entity_name = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:rename"

    def __init__(self, coordinator):
        self.coordinator = coordinator
        serial = coordinator.client.serial or "local"
        self._attr_unique_id = f"{serial}_resync_names"
        self._attr_name = f"Larnitech {serial} resync names"
        self.entity_id = ENTITY_ID_FORMAT.format(f"{serial}_resync_names")

    async def async_press(self) -> None:
        # Refresh names from the controller, then force them onto HA.
        await self.coordinator.async_request_refresh()
        self.coordinator.resync_names()
