"""Support for Eveus switches."""
from __future__ import annotations

import logging
import asyncio
import time
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

# Constants
MAX_RETRIES = 3
RETRY_DELAY = 2
COMMAND_TIMEOUT = 5
UPDATE_TIMEOUT = 10
MIN_UPDATE_INTERVAL = 2
MIN_COMMAND_INTERVAL = 1

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

    # Store switch references for cleanup
    if "entities" not in hass.data[DOMAIN][entry.entry_id]:
        hass.data[DOMAIN][entry.entry_id]["entities"] = {}
    hass.data[DOMAIN][entry.entry_id]["entities"]["switch"] = switches

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
        self._last_update = time.time()  # Initialize with current time
        self._state_data = {}
        self._error_count = 0
        self._max_errors = 3

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"{self._host}_{self.name}"

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self._available

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        return self._is_on

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self._host)},
            "name": "Eveus EV Charger",
            "manufacturer": "Eveus",
            "model": f"Eveus ({self._host})",
            "hw_version": self._state_data.get("verHW", "Unknown"),
            "sw_version": self._state_data.get("verFWMain", "Unknown"),
        }

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        return {
            "last_update": self._last_update,
            "last_command": self._last_command_time,
            "error_count": self._error_count,
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create client session with proper configuration."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10, connect=5)
            connector = aiohttp.TCPConnector(
                limit=1, 
                force_close=True,
                enable_cleanup_closed=True
            )
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector
            )
        return self._session

    async def _send_command(self, command: str, value: int) -> bool:
        """Send command with improved retry logic and rate limiting."""
        current_time = time.time()
        if current_time - self._last_command_time < MIN_COMMAND_INTERVAL:
            await asyncio.sleep(MIN_COMMAND_INTERVAL)

        async with self._command_lock:
            for attempt in range(MAX_RETRIES):
                try:
                    session = await self._get_session()
                    async with session.post(
                        f"http://{self._host}/pageEvent",
                        auth=aiohttp.BasicAuth(self._username, self._password),
                        headers={"Content-type": "application/x-www-form-urlencoded"},
                        data=f"pageevent={command}&{command}={value}",
                        timeout=COMMAND_TIMEOUT,
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
                        self._error_count = 0
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

        if command == "evseEnabled":
            return response_data.get("evseEnabled") == value
        elif command == "oneCharge":
            return response_data.get("oneCharge") == value
        elif command == "rstEM1":
            return True  # Reset commands don't have a specific response to validate

        return False

    async def _handle_command_error(self, error: Exception, attempt: int) -> None:
        """Handle command errors with proper logging."""
        self._error_count += 1
        error_message = str(error) if str(error) else "Unknown error"
        
        if attempt + 1 < MAX_RETRIES:
            _LOGGER.debug(
                "Attempt %d: Failed to send command to %s: %s",
                attempt + 1,
                self._host,
                error_message,
            )
        else:
            self._available = False if self._error_count >= self._max_errors else True
            _LOGGER.error(
                "Failed to send command after %d attempts to %s: %s",
                MAX_RETRIES,
                self._host,
                error_message,
            )

    async def async_update(self) -> None:
        """Update device state."""
        current_time = time.time()
        if self._last_update and current_time - self._last_update < MIN_UPDATE_INTERVAL:
            return

        async with self._update_lock:
            try:
                session = await self._get_session()
                async with session.post(
                    f"http://{self._host}/main",
                    auth=aiohttp.BasicAuth(self._username, self._password),
                    timeout=UPDATE_TIMEOUT,
                ) as response:
                    response.raise_for_status()
                    self._state_data = await response.json()
                    self._available = True
                    self._error_count = 0
                    self._last_update = current_time

            except aiohttp.ClientError as error:
                self._error_count += 1
                self._available = False if self._error_count >= self._max_errors else True
                _LOGGER.error("Error updating state for %s: %s", self.name, str(error))

            except Exception as error:
                self._error_count += 1
                self._available = False if self._error_count >= self._max_errors else True
                _LOGGER.error("Unexpected error updating state for %s: %s", self.name, str(error))
                
    def _validate_state_data(self) -> None:
        """Validate received state data."""
        required_fields = ["state", "evseEnabled", "oneCharge"]
        for field in required_fields:
            if field not in self._state_data:
                raise ValueError(f"Missing required field: {field}")

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
        await super().async_update()
        if self._available and "evseEnabled" in self._state_data:
            self._is_on = self._state_data["evseEnabled"] == 1

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
        await super().async_update()
        if self._available and "oneCharge" in self._state_data:
            self._is_on = self._state_data["oneCharge"] == 1

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
        await super().async_update()
        self._is_on = False  # Always false as it's a momentary switch
