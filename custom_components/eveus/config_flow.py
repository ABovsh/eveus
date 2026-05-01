"""Config flow for Eveus."""
from __future__ import annotations

import logging
import asyncio
import ipaddress
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

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
    CONF_MODEL,
    MODELS,
    MIN_CURRENT,
    MODEL_MAX_CURRENT,
)
from . import CONFIG_ENTRY_VERSION

_LOGGER = logging.getLogger(__name__)
_HOSTNAME_RE = re.compile(r"(?!-)[A-Z\d-]{1,63}(?<!-)$", re.IGNORECASE)


def _is_valid_ip(ip: str) -> bool:
    """Check if string is a valid IP address."""
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


def _is_valid_hostname(hostname: str) -> bool:
    """Validate hostname."""
    if not hostname:
        return False
    if len(hostname) > 255:
        return False
    if hostname[-1] == ".":
        hostname = hostname[:-1]
    if not hostname:
        return False
    return all(_HOSTNAME_RE.match(x) for x in hostname.split("."))


def validate_host(host: str) -> str:
    """Validate host input."""
    host = host.strip()
    if not host:
        raise vol.Invalid("Host cannot be empty")

    if host.startswith(("http://", "https://")):
        parsed = urlparse(host)
        host = parsed.hostname or host

    if not _is_valid_ip(host) and not _is_valid_hostname(host):
        raise vol.Invalid("Invalid IP address or hostname")

    if host.endswith("."):
        host = host[:-1]

    return host


def validate_credentials(username: str, password: str) -> tuple[str, str]:
    """Validate credentials input."""
    username = username.strip()

    if not username or not password:
        raise vol.Invalid("Username and password cannot be empty")
    if len(username) > 32 or len(password) > 32:
        raise vol.Invalid("Username and password must be less than 32 characters")

    return username, password


def validate_device_response(
    result: Any,
    model: str,
) -> dict[str, Any]:
    """Validate that /main returned an Eveus-compatible payload."""
    if not isinstance(result, dict):
        raise CannotConnect("Invalid response format")

    if "currentSet" not in result:
        raise InvalidDevice("Device response is missing currentSet")

    try:
        current_set = float(result["currentSet"])
    except (TypeError, ValueError) as err:
        raise InvalidDevice("Device reports invalid current format") from err

    if current_set < MIN_CURRENT:
        raise InvalidDevice("Device reports invalid current setting")

    max_current = MODEL_MAX_CURRENT.get(model)
    if max_current and current_set > max_current:
        raise InvalidDevice(
            f"Device current ({current_set}A) exceeds model maximum ({max_current}A)"
        )

    return {
        "current_set": current_set,
        "firmware": result.get("verFWMain", "Unknown"),
    }


def normalize_user_input(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize config-flow input before connection validation and storage."""
    host = validate_host(data[CONF_HOST])
    username, password = validate_credentials(data[CONF_USERNAME], data[CONF_PASSWORD])
    model = data.get(CONF_MODEL)
    if model not in MODELS:
        raise vol.Invalid("Invalid charger model")

    return {
        CONF_HOST: host,
        CONF_USERNAME: username,
        CONF_PASSWORD: password,
        CONF_MODEL: model,
    }


def build_user_data_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Build config-flow schema with optional defaults."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST)): str,
            vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME)): str,
            vol.Required(CONF_PASSWORD, default=defaults.get(CONF_PASSWORD)): str,
            vol.Required(
                CONF_MODEL,
                default=defaults.get(CONF_MODEL, MODEL_16A),
            ): vol.In(MODELS),
        }
    )


STEP_USER_DATA_SCHEMA = build_user_data_schema()


def build_reauth_data_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    """Build reauth schema for credential updates."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME)): str,
            vol.Required(CONF_PASSWORD, default=defaults.get(CONF_PASSWORD)): str,
        }
    )


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    try:
        normalized_data = normalize_user_input(data)
    except vol.Invalid as err:
        raise InvalidInput(str(err))

    try:
        session = aiohttp_client.async_get_clientsession(hass)
        timeout = aiohttp.ClientTimeout(total=10)

        async with session.post(
            f"http://{normalized_data[CONF_HOST]}/main",
            auth=aiohttp.BasicAuth(
                normalized_data[CONF_USERNAME],
                normalized_data[CONF_PASSWORD],
            ),
            timeout=timeout,
        ) as response:
            if response.status == 401:
                raise InvalidAuth("Invalid credentials")
            response.raise_for_status()

            try:
                result = await response.json(content_type=None)
            except ValueError:
                raise CannotConnect("Invalid response format")

            device_info = validate_device_response(result, normalized_data[CONF_MODEL])

            return {
                "title": f"Eveus Charger ({normalized_data[CONF_HOST]})",
                "data": normalized_data,
                "device_info": device_info,
            }

    except aiohttp.ClientResponseError as err:
        if err.status == 401:
            raise InvalidAuth from err
        raise CannotConnect(f"Connection error: {err}") from err
    except (asyncio.TimeoutError, aiohttp.ClientError) as err:
        raise CannotConnect(f"Connection error: {err}") from err
    except (InvalidAuth, InvalidDevice, InvalidInput, CannotConnect):
        raise
    except Exception as err:
        _LOGGER.exception("Unexpected error: %s", str(err))
        raise CannotConnect(f"Unexpected error: {err}") from err


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Eveus."""

    VERSION = CONFIG_ENTRY_VERSION

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._host: str | None = None
        self._device_info: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)

                entry_data = info["data"]
                self._host = entry_data[CONF_HOST]
                await self.async_set_unique_id(self._host)
                self._abort_if_unique_id_configured()

                self._device_info = info["device_info"]

                return self.async_create_entry(
                    title=info["title"],
                    data=entry_data,
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
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Allow users to reconfigure connection details."""
        entry = self._get_reconfigure_entry()
        errors = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
                entry_data = info["data"]

                await self.async_set_unique_id(entry_data[CONF_HOST])
                if entry.unique_id != entry_data[CONF_HOST]:
                    self._abort_if_unique_id_configured()

                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=entry_data[CONF_HOST],
                    title=info["title"],
                    data=entry_data,
                )

            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except InvalidInput as err:
                errors["base"] = "invalid_input"
                _LOGGER.error("Invalid reconfigure input: %s", str(err))
            except InvalidDevice as err:
                errors["base"] = "invalid_device"
                _LOGGER.error("Invalid reconfigure device: %s", str(err))
            except Exception:
                _LOGGER.exception("Unexpected reconfigure exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=build_user_data_schema(entry.data),
            errors=errors,
        )

    async def async_step_reauth(
        self,
        entry_data: Mapping[str, Any],
    ) -> FlowResult:
        """Handle reauthentication when stored credentials fail."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Update credentials for an existing Eveus charger."""
        entry = self._get_reauth_entry()
        errors = {}

        if user_input is not None:
            try:
                merged_data = dict(entry.data)
                merged_data[CONF_USERNAME] = user_input[CONF_USERNAME]
                merged_data[CONF_PASSWORD] = user_input[CONF_PASSWORD]

                info = await validate_input(self.hass, merged_data)
                entry_data = info["data"]

                await self.async_set_unique_id(entry_data[CONF_HOST])
                if entry.unique_id != entry_data[CONF_HOST]:
                    return self.async_abort(reason="wrong_device")

                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=entry_data[CONF_HOST],
                    title=info["title"],
                    data=entry_data,
                )

            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except InvalidInput as err:
                errors["base"] = "invalid_input"
                _LOGGER.error("Invalid reauth input: %s", str(err))
            except InvalidDevice as err:
                errors["base"] = "invalid_device"
                _LOGGER.error("Invalid reauth device: %s", str(err))
            except Exception:
                _LOGGER.exception("Unexpected reauth exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=build_reauth_data_schema(entry.data),
            errors=errors,
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect to the device."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid authentication."""


class InvalidInput(HomeAssistantError):
    """Error to indicate invalid user input."""


class InvalidDevice(HomeAssistantError):
    """Error to indicate invalid device response or capabilities."""
