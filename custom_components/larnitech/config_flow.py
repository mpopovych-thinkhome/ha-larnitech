# Updated: 2026-06-24 15:55
"""Config, options and reconfigure flows for Larnitech."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.util.ssl import client_context

from .client import LarnitechAuthError, LarnitechClient, LarnitechError
from .const import (
    CONF_AUTO_REMOVE,
    CONF_CONNECTION_TYPE,
    CONF_HOST,
    CONF_KEY,
    CONF_PORT,
    CONF_SERIAL,
    CONF_UPDATE_AREAS,
    CONF_UPDATE_NAMES,
    CONF_USE_AREAS,
    CONN_CLOUD,
    CONN_LOCAL,
    DEFAULT_LOCAL_PORT,
    DOMAIN,
    TOGGLE_DEFAULTS,
)

_TOGGLE_KEYS = (CONF_AUTO_REMOVE, CONF_UPDATE_NAMES, CONF_USE_AREAS, CONF_UPDATE_AREAS)


def _connection_schema(d: dict) -> dict:
    return {
        vol.Required(CONF_CONNECTION_TYPE, default=d.get(CONF_CONNECTION_TYPE, CONN_CLOUD)):
            vol.In([CONN_CLOUD, CONN_LOCAL]),
        vol.Optional(CONF_SERIAL, default=d.get(CONF_SERIAL, "")): str,
        vol.Optional(CONF_HOST, default=d.get(CONF_HOST, "")): str,
        vol.Optional(CONF_PORT, default=d.get(CONF_PORT, DEFAULT_LOCAL_PORT)): int,
        vol.Required(CONF_KEY, default=d.get(CONF_KEY, "")): str,
    }


def _toggles_schema(d: dict) -> dict:
    return {
        vol.Required(k, default=d.get(k, TOGGLE_DEFAULTS[k])): bool for k in _TOGGLE_KEYS
    }


async def _test_connection(hass, data: dict) -> str | None:
    """Return an error key, or None on success."""
    ssl_context = None
    if data[CONF_CONNECTION_TYPE] == CONN_CLOUD:
        ssl_context = await hass.async_add_executor_job(client_context)
    client = LarnitechClient(
        connection_type=data[CONF_CONNECTION_TYPE],
        key=data[CONF_KEY],
        serial=data.get(CONF_SERIAL) or None,
        host=data.get(CONF_HOST) or None,
        port=data.get(CONF_PORT, DEFAULT_LOCAL_PORT),
        ssl_context=ssl_context,
    )
    try:
        await client.async_test()
    except LarnitechAuthError:
        return "invalid_auth"
    except LarnitechError:
        return "cannot_connect"
    finally:
        await client.async_close()
    return None


def _uid(data: dict) -> str:
    return data.get(CONF_SERIAL) or f"{data.get(CONF_HOST)}:{data.get(CONF_PORT)}"


class LarnitechConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await _test_connection(self.hass, user_input)
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(_uid(user_input))
                self._abort_if_unique_id_configured()
                options = {k: user_input.pop(k) for k in _TOGGLE_KEYS}
                return self.async_create_entry(
                    title=f"Larnitech {_uid(user_input)}", data=user_input, options=options
                )

        schema = vol.Schema({**_connection_schema({}), **_toggles_schema({})})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await _test_connection(self.hass, user_input)
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(entry, data_updates=user_input)

        schema = vol.Schema(_connection_schema(entry.data))
        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return LarnitechOptionsFlow()


class LarnitechOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(_toggles_schema(current))
        )
