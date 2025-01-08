"""Support for Eveus number entities."""
from __future__ import annotations

import logging
import asyncio
import time
import aiohttp
from typing import Any

from homeassistant.components.number import (
    NumberEntity,
    NumberMode,
    NumberDeviceClass,
    RestoreNumber,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.const import (
    CONF_HOST,
    CONF_USERNAME,
    CONF_PASSWORD,
    UnitOfElectricCurrent,
)

from .const import (
    DOMAIN,
    MODEL_MAX_CURRENT,
    MIN_CURRENT,
    CONF_MODEL,
)

_LOGGER = logging.getLogger(__name__)

# Constants for retry and timing
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
    """Set up the Eveus number entities."""
    host = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    model = entry.data[CONF_MODEL]

    entities = [
        EveusCurrentNumber(host, username, password, model),
    ]
    
    # Store entity references for cleanup
    if "entities" not in hass.data[DOMAIN][entry.entry_id]:
        hass.data[DOMAIN][entry.entry_id]["entities"] = {}
    hass.data[DOMAIN][entry.entry_id]["entities"]["number"] = entities

    async_add_entities(entities)

class EveusCurrentNumber(RestoreNumber):
    """Representation of Eveus current control."""

    _attr_native_step = 1.0
    _attr_mode = NumberMode.SLIDER
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_device_class = NumberDeviceClass.CURRENT
    _attr_has_entity_name = True
    _attr_name = "Charging Current"
    _attr_icon = "mdi:current-ac"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, host: str, username: str, password: str, model: str) -> None:
        """Initialize the current control."""
        super().__init__()
        self._host = host
        self._username = username
        self._password = password
        self._model = model
        self._attr_unique_id = f"{host}_charging_current"
        self._session = None
        self._last_update = 0
        self._last_command = 0
        self._update_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()
        self._attr_available = True
        self._error_count = 0
        self._max_errors = 3
        self._state_data = {}

        # Set min/max values based on model
        self._attr_native_min_value = float(MIN_CURRENT)
        self._attr_native_max_value = float(MODEL_MAX_CURRENT[model])
        self._value = min(self._attr_native_max_value, 16.0)  # Default to 16A or max if lower

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self._host)},
            "name": "Eveus EV Charger",
            "manufacturer": "Eveus",
            "model": f"Eveus {self._model} ({self._host})",
            "hw_version": self._state_data.get("verHW", "Unknown"),
            "sw_version": self._state_data.get("verFWMain", "Unknown"),
        }

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        return {
            "last_update": self._last_update,
            "last_command": self._last_command,
            "error_count": self._error_count,
            "min_current": self._attr_native_min_value,
            "max_current": self._attr_native_max_value,
            "model": self._model,
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create client session with proper configuration."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10, connect=5)
            connector = aiohttp.TCPConnector(limit=1, force_close=True)
            self._session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return self._session

    async def async_set_native_value(self, value: float) -> None:
        """Set new current value with retry logic."""
        # Ensure value is within bounds
        value = min(self._attr_native_max_value, max(self._attr_native_min_value, value))
        
        # Rate limiting
        current_time = time.time()
        if current_time - self._last_command < MIN_COMMAND_INTERVAL:
            await asyncio.sleep(MIN_COMMAND_INTERVAL)
        
        async with self._command_lock:
            for attempt in range(MAX_RETRIES):
                try:
                    session = await self._get_session()
                    async with session.post(
                        f"http://{self._host}/pageEvent",
                        auth=aiohttp.BasicAuth(self._username, self._password),
                        headers={"Content-type": "application/x-www-form-urlencoded"},
                        data=f"currentSet={int(value)}",
                        timeout=COMMAND_TIMEOUT,
                    ) as response:
                        response.raise_for_status()
                        
                        # Validate response
                        response_data = await response.json()
                        if not self._validate_command_response(response_data, value):
                            raise ValueError("Invalid command response")
                            
                        self._value = value
                        self._attr_available = True
                        self._last_command = time.time()
                        self._error_count = 0
                        
                        _LOGGER.debug(
                            "Successfully set charging current to %s A for %s",
                            value,
                            self._host,
                        )
                        return

                except Exception as error:
                    await self._handle_command_error(error, attempt)
                    if attempt + 1 >= MAX_RETRIES:
                        break
                    await asyncio.sleep(RETRY_DELAY * (attempt + 1))

    def _validate_command_response(self, response_data: dict, value: float) -> bool:
        """Validate command response data."""
        if not isinstance(response_data, dict):
            return False

        try:
            current_set = float(response_data.get("currentSet", 0))
            # Allow small difference due to rounding
            return abs(current_set - value) < 0.1
        except (TypeError, ValueError):
            return False

    async def _handle_command_error(self, error: Exception, attempt: int) -> None:
        """Handle command errors with proper logging."""
        self._error_count += 1
        error_message = str(error) if str(error) else "Unknown error"
        
        if attempt + 1 < MAX_RETRIES:
            _LOGGER.debug(
                "Attempt %d: Failed to set charging current for %s: %s",
                attempt + 1,
                self._host,
                error_message,
            )
        else:
            self._attr_available = False if self._error_count >= self._max_errors else True
            _LOGGER.error(
                "Failed to set charging current after %d attempts for %s: %s",
                MAX_RETRIES,
                self._host,
                error_message,
            )

    async def async_update(self) -> None:
        """Update the current value with retry logic."""
        # Rate limiting
        current_time = time.time()
        if current_time - self._last_update < MIN_UPDATE_INTERVAL:
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
                    
                    # Validate and update value
                    if "currentSet" in self._state_data:
                        current_set = float(self._state_data["currentSet"])
                        self._value = min(self._attr_native_max_value, 
                                        max(self._attr_native_min_value, current_set))
                        
                    self._attr_available = True
                    self._last_update = time.time()
                    self._error_count = 0

            except Exception as error:
                self._error_count += 1
                self._attr_available = False if self._error_count >= self._max_errors else True
                _LOGGER.error(
                    "Failed to update charging current for %s: %s",
                    self._host,
                    str(error),
                )

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in ('unknown', 'unavailable'):
            try:
                restored_value = float(last_state.state)
                self._value = min(self._attr_native_max_value, 
                               max(self._attr_native_min_value, restored_value))
            except (TypeError, ValueError):
                self._value = min(self._attr_native_max_value, 16.0)  # Default to 16A or max if lower

    async def async_will_remove_from_hass(self) -> None:
        """Clean up resources when entity is removed."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
