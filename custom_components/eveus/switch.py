"""Support for Eveus switches."""
from __future__ import annotations

import logging
import asyncio
import aiohttp
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.const import (
    CONF_HOST,
    CONF_USERNAME,
    CONF_PASSWORD,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Constants for retry mechanism
MAX_RETRIES = 3
RETRY_DELAY = 2
COMMAND_TIMEOUT = 5

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eveus switches based on config entry."""
    host = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    switches = [
        EveusStopChargingSwitch(host, username, password),
        EveusOneChargeSwitch(host, username, password),
        EveusResetCounterASwitch(host, username, password),
    ]

    async_add_entities(switches)

class BaseEveusSwitch(SwitchEntity):
    """Base class for Eveus switches with improved error handling."""

    def __init__(self, host: str, username: str, password: str) -> None:
        """Initialize the switch."""
        self._host = host
        self._username = username
        self._password = password
        self._available = True
        self._session = None
        self._is_on = False
        self._attr_has_entity_name = True
        self._command_lock = asyncio.Lock()
        self._update_lock = asyncio.Lock()
        self._last_command_time = 0
        self._min_command_interval = 1  # Minimum seconds between commands
        self._last_successful_update = 0
        self._command_timeout = 5
        self._update_timeout = 10

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create client session with proper configuration."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10, connect=5)
            connector = aiohttp.TCPConnector(limit=1, force_close=True)
            self._session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return self._session

    async def _send_command(self, command: str, value: int) -> bool:
        """Send command with improved retry logic and rate limiting."""
        current_time = time.time()
        if current_time - self._last_command_time < self._min_command_interval:
            await asyncio.sleep(self._min_command_interval)

        async with self._command_lock:
            for attempt in range(MAX_RETRIES):
                try:
                    session = await self._get_session()
                    async with session.post(
                        f"http://{self._host}/pageEvent",
                        auth=aiohttp.BasicAuth(self._username, self._password),
                        headers={"Content-type": "application/x-www-form-urlencoded"},
                        data=f"pageevent={command}&{command}={value}",
                        timeout=self._command_timeout,
                    ) as response:
                        response.raise_for_status()
                        
                        # Validate response
                        try:
                            response_data = await response.json()
                            if not self._validate_command_response(response_data, command, value):
                                raise ValueError("Invalid command response")
                        except Exception as validation_err:
                            _LOGGER.warning(
                                "Command validation failed: %s", str(validation_err)
                            )
                            raise

                        self._available = True
                        self._last_command_time = current_time
                        _LOGGER.debug(
                            "Successfully sent command %s=%s to %s",
                            command,
                            value,
                            self._host,
                        )
                        return True

                except Exception as error:
                    await self._handle_command_error(error, attempt)
                    if attempt + 1 >= MAX_RETRIES:
                        return False
                    await asyncio.sleep(RETRY_DELAY * (attempt + 1))

            return False

    def _validate_command_response(self, response_data: dict, command: str, value: int) -> bool:
        """Validate command response data."""
        if not isinstance(response_data, dict):
            return False

        # Add specific validation logic based on command type
        if command == "evseEnabled":
            return response_data.get("evseEnabled") == value
        elif command == "oneCharge":
            return response_data.get("oneCharge") == value
        elif command == "rstEM1":
            return True  # Reset commands don't have a specific response to validate

        return False

    async def _handle_command_error(self, error: Exception, attempt: int) -> None:
        """Handle command errors with proper logging."""
        error_message = str(error) if str(error) else "Unknown error"
        
        if attempt + 1 < MAX_RETRIES:
            _LOGGER.debug(
                "Attempt %d: Failed to send command to %s: %s",
                attempt + 1,
                self._host,
                error_message,
            )
        else:
            self._available = False
            _LOGGER.error(
                "Failed to send command after %d attempts to %s: %s",
                MAX_RETRIES,
                self._host,
                error_message,
            )

    async def async_will_remove_from_hass(self) -> None:
        """Clean up resources when entity is removed."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

class EveusStopChargingSwitch(BaseEveusSwitch):
    """Representation of Eveus charging control switch."""

    _attr_name = "Stop Charging"
    _attr_icon = "mdi:ev-station"
    _attr_entity_category = EntityCategory.CONFIG

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on charging."""
        if await self._send_command("evseEnabled", 1):
            self._is_on = True

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off charging."""
        if await self._send_command("evseEnabled", 0):
            self._is_on = False

    async def async_update(self) -> None:
        """Update state."""
        state = await self._get_state("evseEnabled")
        if state is not None:
            self._is_on = state

class EveusOneChargeSwitch(BaseEveusSwitch):
    """Representation of Eveus one charge switch."""

    _attr_name = "One Charge"
    _attr_icon = "mdi:lightning-bolt"
    _attr_entity_category = EntityCategory.CONFIG

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable one charge mode."""
        if await self._send_command("oneCharge", 1):
            self._is_on = True

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable one charge mode."""
        if await self._send_command("oneCharge", 0):
            self._is_on = False

    async def async_update(self) -> None:
        """Update state."""
        state = await self._get_state("oneCharge")
        if state is not None:
            self._is_on = state

class EveusResetCounterASwitch(BaseEveusSwitch):
    """Representation of Eveus reset counter A switch."""

    _attr_name = "Reset Counter A"
    _attr_icon = "mdi:counter"
    _attr_entity_category = EntityCategory.CONFIG

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Reset counter A."""
        if await self._send_command("rstEM1", 0):
            self._is_on = False  # Always false as it's a momentary switch

    async def async_turn_off(self, **kwargs: Any) -> None:
        """No-op for reset switch."""
        self._is_on = False

    async def async_update(self) -> None:
        """Update state."""
        self._is_on = False  # Always false as it's a momentary switch
