"""Config flow for Eveus."""
from __future__ import annotations

import logging
import asyncio
import socket
import re
from typing import Any
from urllib.parse import urlparse

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import aiohttp_client

from .const import (
    DOMAIN,
    MODEL_16A,
    MODEL_32A,
    CONF_MODEL,
    MODELS,
    MIN_CURRENT,
    MODEL_MAX_CURRENT,
)

_LOGGER = logging.getLogger(__name__)

def is_valid_ip(ip: str) -> bool:
    """Check if string is valid IP address."""
    try:
        socket.inet_aton(ip)
        return True
    except socket.error:
        return False

def is_valid_hostname(hostname: str) -> bool:
    """Validate hostname."""
    if len(hostname) > 255:
        return False
    if hostname[-1] == ".":
        hostname = hostname[:-1]
    allowed = re.compile(r"(?!-)[A-Z\d-]{1,63}(?<!-)$", re.IGNORECASE)
    return all(allowed.match(x) for x in hostname.split("."))

def validate_host(host: str) -> str:
    """Validate host input."""
    host = host.strip()
    if not host:
        raise vol.Invalid("Host cannot be empty")
    
    # Remove protocol if present
    if host.startswith(("http://", "https://")):
        parsed = urlparse(host)
        host = parsed.hostname or host

    # Validate IP or hostname
    if not is_valid_ip(host) and not is_valid_hostname(host):
        raise vol.Invalid("Invalid IP address or hostname")
        
    return host

def validate_credentials(username: str, password: str) -> tuple[str, str]:
    """Validate credentials input."""
    username = username.strip()
    password = password.strip()
    
    if not username or not password:
        raise vol.Invalid("Username and password cannot be empty")
    
    if len(username) > 32 or len(password) > 32:
        raise vol.Invalid("Username and password must be less than 32 characters")
        
    return username, password

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
    # Validate host format
    try:
        host = validate_host(data[CONF_HOST])
        username, password = validate_credentials(
            data[CONF_USERNAME], 
            data[CONF_PASSWORD]
        )
    except vol.Invalid as err:
        raise InvalidInput(str(err))

    try:
        session = aiohttp_client.async_get_clientsession(hass)
        timeout = aiohttp.ClientTimeout(total=10)

        async with session.post(
            f"http://{host}/main",
            auth=aiohttp.BasicAuth(username, password),
            timeout=timeout,
        ) as response:
            if response.status == 401:
                raise InvalidAuth("Invalid credentials")
            response.raise_for_status()
            
            try:
                result = await response.json()
            except ValueError:
                raise CannotConnect("Invalid response format")
                
            if not isinstance(result, dict):
                raise CannotConnect("Invalid response format")
                
            # Validate device capabilities
            current_set = result.get("currentSet")
            if current_set is not None:
                try:
                    current_set = float(current_set)
                    if current_set < MIN_CURRENT:
                        raise InvalidDevice("Device reports invalid current setting")
                except ValueError:
                    raise InvalidDevice("Device reports invalid current format")
            
            # Validate model compatibility
            max_current = MODEL_MAX_CURRENT.get(data[CONF_MODEL])
            if max_current and current_set and current_set > max_current:
                raise InvalidDevice(
                    f"Device current ({current_set}A) exceeds model maximum ({max_current}A)"
                )
            
            return {
                "title": f"Eveus Charger ({host})",
                "device_info": {
                    "current_set": current_set,
                    "firmware": result.get("verFWMain", "Unknown"),
                }
            }

    except aiohttp.ClientResponseError as err:
        if err.status == 401:
            raise InvalidAuth from err
        raise CannotConnect(f"Connection error: {err}") from err
    except (asyncio.TimeoutError, aiohttp.ClientError) as err:
        raise CannotConnect(f"Connection error: {err}") from err
    except Exception as err:
        _LOGGER.exception("Unexpected error: %s", str(err))
        raise CannotConnect(f"Unexpected error: {err}") from err

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Eveus."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._host: str | None = None
        self._username: str | None = None
        self._device_info: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
                
                # Check if device is already configured
                self._host = user_input[CONF_HOST]
                await self.async_set_unique_id(self._host)
                self._abort_if_unique_id_configured()

                self._device_info = info["device_info"]
                return self.async_create_entry(
                    title=info["title"],
                    data=user_input
                )

            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except InvalidInput as err:
                errors["base"] = "invalid_input"
                _LOGGER.error("Invalid input: %s", str(err))
            except InvalidDevice as err:
                errors["base"] = "invalid_device"
                _LOGGER.error("Invalid device: %s", str(err))
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)

class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Eveus integration."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage basic options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({})
        )

class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect to the device."""

class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid authentication."""

class InvalidInput(HomeAssistantError):
    """Error to indicate invalid user input."""

class InvalidDevice(HomeAssistantError):
    """Error to indicate invalid device response or capabilities."""
