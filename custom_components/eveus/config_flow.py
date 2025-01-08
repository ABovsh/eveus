"""Config flow for Eveus."""
from __future__ import annotations

import logging
import ipaddress
import re
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

# Constants
DEFAULT_TIMEOUT = 10
CONNECTION_TIMEOUT = 5
VALIDATION_TIMEOUT = 10

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_MODEL, default=MODEL_16A): vol.In(MODELS),
    }
)

def validate_ip_address(ip_string: str) -> bool:
    """Validate IP address format."""
    try:
        ipaddress.ip_address(ip_string)
        return True
    except ValueError:
        return False

def validate_credentials(username: str, password: str) -> bool:
    """Validate username and password format."""
    # Require at least 4 characters for each
    if len(username) < 4 or len(password) < 4:
        return False
    
    # Allow only alphanumeric and some special characters
    pattern = re.compile(r'^[a-zA-Z0-9@._-]+$')
    return bool(pattern.match(username)) and bool(pattern.match(password))

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

    # Validate current settings
    if "curDesign" in data:
        cur_design = float(data["curDesign"])
        if not 6 <= cur_design <= 32:
            raise ValueError(f"Invalid current design value: {cur_design}")

async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    # Validate IP address format
    if not validate_ip_address(data[CONF_HOST]):
        raise InvalidInput("Invalid IP address format")

    # Validate credentials format
    if not validate_credentials(data[CONF_USERNAME], data[CONF_PASSWORD]):
        raise InvalidAuth("Invalid username or password format")

    # Set proper timeout and connection handling
    timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)
    connector = aiohttp.TCPConnector(
        limit=1,
        force_close=True,
        enable_cleanup_closed=True
    )
    
    try:
        async with aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            raise_for_status=True
        ) as session:
            # Test basic connectivity first
            try:
                async with session.get(
                    f"http://{data[CONF_HOST]}",
                    timeout=CONNECTION_TIMEOUT
                ) as response:
                    if response.status == 404:
                        # 404 is acceptable as it means the server is responding
                        pass
                    elif response.status != 200:
                        raise CannotConnect
            except aiohttp.ClientError as error:
                _LOGGER.error("Connection test failed: %s", str(error))
                raise CannotConnect from error
            
            # Test authentication and get device data
            try:
                async with session.post(
                    f"http://{data[CONF_HOST]}/main",
                    auth=aiohttp.BasicAuth(data[CONF_USERNAME], data[CONF_PASSWORD]),
                    timeout=VALIDATION_TIMEOUT
                ) as response:
                    if response.status == 401:
                        raise InvalidAuth
                    
                    try:
                        response_data = await response.json()
                        validate_device_data(response_data)
                    except ValueError as err:
                        _LOGGER.error("Device data validation failed: %s", str(err))
                        raise InvalidDeviceData(str(err))
                    except (aiohttp.ContentTypeError, ValueError) as err:
                        _LOGGER.error("Invalid response format: %s", str(err))
                        raise InvalidDeviceData("Invalid response format")
                    
                    # Extract device info
                    model = data[CONF_MODEL]
                    firmware = response_data.get("verFWMain", "Unknown")
                    hardware = response_data.get("verHW", "Unknown")
                    device_type = response_data.get("typeEvse", "Unknown")
                    
                    # Validate model compatibility
                    if device_type == 1 and model == MODEL_32A:
                        raise InvalidDeviceData("Device is 16A model but configured as 32A")
                    
                    return {
                        "title": f"Eveus {model} ({data[CONF_HOST]})",
                        "firmware_version": firmware,
                        "hardware_version": hardware,
                        "device_type": device_type,
                    }

            except aiohttp.ClientResponseError as error:
                if error.status == 401:
                    raise InvalidAuth from error
                _LOGGER.error("Device validation failed: %s", str(error))
                raise CannotConnect from error
            
    except aiohttp.ClientError as error:
        _LOGGER.error("Connection failed: %s", str(error))
        raise CannotConnect from error
    except asyncio.TimeoutError as error:
        _LOGGER.error("Connection timed out: %s", str(error))
        raise CannotConnect from error
    finally:
        if connector and not connector.closed:
            await connector.close()

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Eveus."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Check if device is already configured
            for entry in self._async_current_entries():
                if entry.data[CONF_HOST] == user_input[CONF_HOST]:
                    return self.async_abort(reason="already_configured")

            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except InvalidInput:
                errors["base"] = "invalid_input"
            except InvalidDeviceData as err:
                _LOGGER.error("Device validation failed: %s", str(err))
                errors["base"] = "invalid_device"
            except Exception as err:
                _LOGGER.exception("Unexpected exception: %s", str(err))
                errors["base"] = "unknown"
            else:
                # Create entry with device info
                return self.async_create_entry(
                    title=info["title"],
                    data={
                        **user_input,
                        "firmware_version": info["firmware_version"],
                        "hardware_version": info["hardware_version"],
                        "device_type": info["device_type"],
                    }
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""

class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""

class InvalidInput(HomeAssistantError):
    """Error to indicate invalid user input."""

class InvalidDeviceData(HomeAssistantError):
    """Error to indicate invalid device data."""
