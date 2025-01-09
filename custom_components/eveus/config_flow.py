"""Config flow for Eveus."""
from __future__ import annotations

import logging
import asyncio
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import aiohttp_client

from .const import (
    DOMAIN,
    MODEL_16A,
    MODEL_32A,
    CONF_MODEL,
    MODELS,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_MODEL, default=MODEL_16A): vol.In(MODELS),
    }
)

REQUIRED_HELPERS = [
    "input_number.ev_battery_capacity",
    "input_number.ev_initial_soc",
    "input_number.ev_soc_correction",
    "input_number.ev_target_soc",
]

async def _validate_helper_entities(hass: HomeAssistant) -> bool:
    """Validate that required helper entities exist."""
    for entity_id in REQUIRED_HELPERS:
        if not hass.states.get(entity_id):
            return False
    return True

async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    if not await _validate_helper_entities(hass):
        _LOGGER.error("Required input_number entities missing")
        raise InvalidInput

    try:
        session = aiohttp_client.async_get_clientsession(hass)
        timeout = aiohttp.ClientTimeout(total=10)

        async with session.post(
            f"http://{data[CONF_HOST]}/main",
            auth=aiohttp.BasicAuth(data[CONF_USERNAME], data[CONF_PASSWORD]),
            timeout=timeout,
        ) as response:
            if response.status == 401:
                raise InvalidAuth
            response.raise_for_status()
            
            result = await response.json()
            if not isinstance(result, dict) or "state" not in result:
                raise CannotConnect
            
            return {"title": f"Eveus Charger ({data[CONF_HOST]})"}

    except aiohttp.ClientResponseError as err:
        if err.status == 401:
            raise InvalidAuth from err
        raise CannotConnect from err
    except (asyncio.TimeoutError, aiohttp.ClientError):
        raise CannotConnect
    except Exception as err:
        _LOGGER.exception("Unexpected error: %s", str(err))
        raise CannotConnect from err

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Eveus."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
                return self.async_create_entry(
                    title=info["title"],
                    data=user_input
                )
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except InvalidInput:
                errors["base"] = "input_missing"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors
        )

class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""

class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""

class InvalidInput(HomeAssistantError):
    """Error to indicate missing input_number entities."""
