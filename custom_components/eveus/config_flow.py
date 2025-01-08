"""Config flow for Eveus."""
from __future__ import annotations

import logging
import ipaddress
import re
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

async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    try:
        # Create session with timeout
        session = aiohttp_client.async_get_clientsession(hass)
        timeout = aiohttp.ClientTimeout(total=10)

        # First try basic GET to check if device is reachable
        try:
            async with session.get(
                f"http://{data[CONF_HOST]}",
                timeout=timeout
            ) as response:
                if response.status == 404:
                    # 404 is actually OK - means device is responding
                    pass
                elif response.status == 401:
                    raise InvalidAuth
                elif response.status != 200:
                    _LOGGER.error("Device returned status %s", response.status)
                    raise CannotConnect
        except aiohttp.ClientError as err:
            _LOGGER.error("Connection error: %s", str(err))
            raise CannotConnect from err

        # Now try authentication
        try:
            async with session.post(
                f"http://{data[CONF_HOST]}/main",
                auth=aiohttp.BasicAuth(data[CONF_USERNAME], data[CONF_PASSWORD]),
                timeout=timeout,
            ) as response:
                if response.status == 401:
                    _LOGGER.error("Authentication failed")
                    raise InvalidAuth
                response.raise_for_status()
                result = await response.json()

                # Basic validation of response
                if not isinstance(result, dict):
                    raise CannotConnect
                
                return {
                    "title": f"Eveus Charger ({data[CONF_HOST]})"
                }

        except aiohttp.ClientResponseError as err:
            if err.status == 401:
                raise InvalidAuth from err
            _LOGGER.error("Response error: %s", str(err))
            raise CannotConnect from err
        except aiohttp.ClientError as err:
            _LOGGER.error("Connection error during auth: %s", str(err))
            raise CannotConnect from err

    except asyncio.TimeoutError as err:
        _LOGGER.error("Timeout connecting to device")
        raise CannotConnect from err
    except Exception as err:
        _LOGGER.exception("Unexpected exception: %s", str(err))
        raise CannotConnect from err

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
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception: %s", err)
                errors["base"] = "unknown"
            else:
                # Check if already configured
                await self.async_set_unique_id(user_input[CONF_HOST])
                self._abort_if_unique_id_configured()

                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
