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

"""Improved validation for config flow."""
async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    # Validate IP address format
    ip_parts = data[CONF_HOST].split('.')
    if len(ip_parts) != 4 or not all(
        part.isdigit() and 0 <= int(part) <= 255 for part in ip_parts
    ):
        raise InvalidInput("Invalid IP address format")

    # Set proper timeout and connection handling
    timeout = aiohttp.ClientTimeout(total=10, connect=5)
    connector = aiohttp.TCPConnector(limit=1, force_close=True)
    
    try:
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            # Test basic connectivity first
            async with session.get(
                f"http://{data[CONF_HOST]}",
                timeout=5
            ) as response:
                if response.status == 404:
                    # 404 is acceptable as it means the server is responding
                    pass
                elif response.status != 200:
                    raise CannotConnect
            
            # Now test authentication
            async with session.post(
                f"http://{data[CONF_HOST]}/main",
                auth=aiohttp.BasicAuth(data[CONF_USERNAME], data[CONF_PASSWORD]),
                timeout=10
            ) as response:
                if response.status == 401:
                    raise InvalidAuth
                response.raise_for_status()
                
                # Validate response data
                try:
                    response_data = await response.json()
                    validate_device_data(response_data)
                except ValueError as err:
                    raise InvalidDeviceData(str(err))
                
                # Get device info for title
                model = data[CONF_MODEL]
                firmware = response_data.get("verFWMain", "Unknown")
                
                return {
                    "title": f"Eveus {model} ({data[CONF_HOST]})",
                    "firmware": firmware,
                }
                
    except aiohttp.ClientResponseError as error:
        if error.status == 401:
            raise InvalidAuth from error
        raise CannotConnect from error
    except (aiohttp.ClientError, TimeoutError) as error:
        raise CannotConnect from error
    finally:
        if connector and not connector.closed:
            await connector.close()

def validate_device_data(data: dict) -> None:
    """Validate device response data."""
    required_fields = ["state", "currentSet", "evseEnabled"]
    for field in required_fields:
        if field not in data:
            raise ValueError(f"Missing required field: {field}")

    # Validate device type if available
    if "typeEvse" in data:
        evse_type = data["typeEvse"]
        if not isinstance(evse_type, (int, float)) or evse_type not in [1, 2]:
            raise ValueError(f"Invalid EVSE type: {evse_type}")

class InvalidDeviceData(HomeAssistantError):
    """Error to indicate invalid device data."""

class InvalidInput(HomeAssistantError):
    """Error to indicate invalid user input."""
