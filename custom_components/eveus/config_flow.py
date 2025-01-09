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

REQUIRED_HELPERS = [
    "input_number.ev_battery_capacity",
    "input_number.ev_initial_soc",
    "input_number.ev_soc_correction",
    "input_number.ev_target_soc",
]

async def _validate_helper_entities(hass: HomeAssistant) -> tuple[bool, list[str]]:
    """Validate that required helper entities exist."""
    missing_entities = []
    
    # Check for entities in both states and entity registry
    for entity_id in REQUIRED_HELPERS:
        if (not hass.states.get(entity_id) and 
            not hass.helpers.entity_registry.async_get(entity_id)):
            missing_entities.append(entity_id)

    return not bool(missing_entities), missing_entities

async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    # First check helper entities
    helpers_valid, missing_helpers = await _validate_helper_entities(hass)
    if not helpers_valid:
        readable_names = [entity.split('.')[1].replace('_', ' ').title() 
                         for entity in missing_helpers]
        message = (
            f"Missing required helper entities: {', '.join(readable_names)}. "
            "Please create these helpers before adding the integration. "
            "See documentation for setup instructions."
        )
        _LOGGER.error(message)
        raise InvalidHelperEntities(message)

    try:
        # Create session with timeout
        session = aiohttp_client.async_get_clientsession(hass)
        timeout = aiohttp.ClientTimeout(total=30)

        try:
            # First try POST with auth
            async with session.post(
                f"http://{data[CONF_HOST]}/main",
                auth=aiohttp.BasicAuth(data[CONF_USERNAME], data[CONF_PASSWORD]),
                timeout=timeout,
            ) as response:
                if response.status == 401:
                    _LOGGER.error("Authentication failed - invalid credentials")
                    raise InvalidAuth
                
                response.raise_for_status()
                result = await response.json()

                # Basic validation of response
                if not isinstance(result, dict):
                    _LOGGER.error("Invalid response from device - not a JSON object")
                    raise CannotConnect

                # Verify we have basic state field
                if "state" not in result:
                    _LOGGER.error("Missing required state field in response")
                    raise CannotConnect

                return {
                    "title": f"Eveus Charger ({data[CONF_HOST]})",
                }

        except aiohttp.ClientResponseError as err:
            if err.status == 401:
                _LOGGER.error("Authentication error: %s", str(err))
                raise InvalidAuth from err
            _LOGGER.error("HTTP error: %s", str(err))
            raise CannotConnect from err
            
        except aiohttp.ClientError as err:
            _LOGGER.error("Connection error during auth: %s", str(err))
            raise CannotConnect from err
            
        except ValueError as err:
            _LOGGER.error("Data validation error: %s", str(err))
            raise CannotConnect from err

    except asyncio.TimeoutError as err:
        _LOGGER.error("Timeout connecting to device at %s: %s", data[CONF_HOST], str(err))
        raise CannotConnect from err
        
    except Exception as err:
        _LOGGER.exception("Unexpected error during validation: %s", str(err))
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
                # Check if already configured
                self._async_abort_entries_match({CONF_HOST: user_input[CONF_HOST]})
                
                info = await validate_input(self.hass, user_input)
                
                return self.async_create_entry(
                    title=info["title"],
                    data=user_input
                )
                
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except InvalidHelperEntities as err:
                errors["base"] = "invalid_helper_entities"
                # Store the error message for the UI
                self.context["error_detail"] = str(err)
            except Exception as err:
                _LOGGER.exception("Unexpected exception: %s", err)
                errors["base"] = "unknown"

        # Include error detail in the form data if available
        error_detail = self.context.get("error_detail", "")
        
        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "error_detail": error_detail,
            }
        )

class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""

class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""

class InvalidHelperEntities(HomeAssistantError):
    """Error to indicate missing helper entities."""
