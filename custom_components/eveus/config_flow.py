"""Config flow for Eveus."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    MODEL_16A,
    MODEL_32A,
    CONF_MODEL,
    CONF_BATTERY_CAPACITY,
    MODELS,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_MODEL, default=MODEL_16A): vol.In(MODELS),
        vol.Required(CONF_BATTERY_CAPACITY, default=60): vol.All(
            vol.Coerce(int),
            vol.Range(min=10, max=160)
        ),
    }
)

async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"http://{data[CONF_HOST]}/main",
                auth=aiohttp.BasicAuth(data[CONF_USERNAME], data[CONF_PASSWORD]),
                timeout=10
            ) as response:
                if response.status == 401:
                    raise InvalidAuth
                response.raise_for_status()
                await response.json()
                
                # Create helper entity for battery capacity
                hass.states.async_set(
                    f"input_number.ev_battery_capacity",
                    data[CONF_BATTERY_CAPACITY],
                    {
                        "friendly_name": "EV Battery Capacity",
                        "unit_of_measurement": "kWh",
                        "icon": "mdi:car-battery",
                        "min": 10,
                        "max": 160,
                        "step": 1,
                    }
                )
                
    except aiohttp.ClientResponseError as error:
        if error.status == 401:
            raise InvalidAuth from error
        raise CannotConnect from error
    except (aiohttp.ClientError, TimeoutError) as error:
        raise CannotConnect from error
    
    return {"title": f"Eveus Charger ({data[CONF_HOST]})"}

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Eveus."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""

class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
