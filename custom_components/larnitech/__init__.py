# Updated: 2026-06-24 15:55
"""The Larnitech integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.util.ssl import client_context

from .client import LarnitechClient, LarnitechError
from .const import (
    CONF_CONNECTION_TYPE,
    CONF_IP,
    CONF_KEY,
    CONF_PORT,
    CONF_SERIAL,
    CONN_CLOUD,
    DEFAULT_LOCAL_PORT,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import LarnitechCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ssl_context = None
    if entry.data[CONF_CONNECTION_TYPE] == CONN_CLOUD:
        # Build the SSL context off the event loop (cert loading is blocking).
        ssl_context = await hass.async_add_executor_job(client_context)

    client = LarnitechClient(
        connection_type=entry.data[CONF_CONNECTION_TYPE],
        key=entry.data[CONF_KEY],
        serial=entry.data.get(CONF_SERIAL),
        host=entry.data.get(CONF_IP),
        port=entry.data.get(CONF_PORT, DEFAULT_LOCAL_PORT),
        ssl_context=ssl_context,
    )
    coordinator = LarnitechCoordinator(hass, client, entry)
    # Decoded events are applied directly (no re-read); a (re)connect pulls a
    # full snapshot to resync.
    client.set_event_callback(coordinator.apply_events)
    client.set_resync_callback(
        lambda: hass.async_create_task(coordinator.async_request_refresh())
    )

    try:
        await client.async_start()
    except LarnitechError as err:
        raise ConfigEntryNotReady(str(err)) from err

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload_on_update))
    return True


async def _reload_on_update(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options (toggles) change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.client.async_close()
    return unload_ok
