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
from homeassistant.core import HomeAssistant
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
    """Set up the Eveus number entities."""
    host = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    model = entry.data[CONF_MODEL]

    entities = [
        EveusCurrentNumber(host, username, password, model),
    ]

    # Add entity configuration
    for entity in entities:
        entity._attr_entity_registry_enabled_default = True
        entity._attr_entity_registry_visible_default = True
        entity._attr_has_entity_name = True

    async_add_entities(entities)

    # Store references
    if "entities" not in hass.data[DOMAIN][entry.entry_id]:
        hass.data[DOMAIN][entry.entry_id]["entities"] = {}
    # Store as dict with unique_id as key
    hass.data[DOMAIN][entry.entry_id]["entities"]["number"] = {
        entity.unique_id: entity for entity in entities
    }

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
        self._session = None
        self._last_update = time.time()
        self._last_command = 0
        self._update_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()
        self._attr_available = True
        self._error_count = 0
        self._max_errors = 3
        self._attr_unique_id = f"{host}_charging_current"

        # Set min/max values based on model
        self._attr_native_min_value = float(MIN_CURRENT)
        self._attr_native_max_value = float(MODEL_MAX_CURRENT[model])
        self._value = min(self._attr_native_max_value, 16.0)

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self._host)},
            "name": "Eveus EV Charger",
            "manufacturer": "Eveus",
            "model": f"Eveus ({self._host})",
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create client session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10, connect=5)
            connector = aiohttp.TCPConnector(limit=1, force_close=True)
            self._session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return self._session

    async def async_set_native_value(self, value: float) -> None:
        """Set new current value with retry logic."""
        value = min(self._attr_native_max_value, max(self._attr_native_min_value, value))
        
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

                        # Get response text instead of trying to parse JSON
                        response_text = await response.text()
                        if "error" in response_text.lower():
                            raise ValueError(f"Error in response: {response_text}")

                        # Verify command success via main endpoint
                        async with session.post(
                            f"http://{self._host}/main",
                            auth=aiohttp.BasicAuth(self._username, self._password),
                            timeout=COMMAND_TIMEOUT,
                        ) as verify_response:
                            verify_response.raise_for_status()
                            verify_data = await verify_response.json()
                            if abs(float(verify_data.get("currentSet", 0)) - value) > 0.1:
                                raise ValueError("Command verification failed")

                        self._value = value
                        self._attr_available = True
                        self._error_count = 0
                        return

                except aiohttp.ClientError as err:
                    if "Connection reset by peer" in str(err) and attempt + 1 < MAX_RETRIES:
                        await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                        continue
                    raise

                except Exception as error:
                    if attempt + 1 >= MAX_RETRIES:
                        self._error_count += 1
                        self._attr_available = False if self._error_count >= self._max_errors else True
                        _LOGGER.error(
                            "Failed to set charging current after %d attempts for %s: %s",
                            MAX_RETRIES,
                            self._host,
                            str(error),
                        )
                        return
                    await asyncio.sleep(RETRY_DELAY * (attempt + 1))

    async def async_update(self) -> None:
        """Update the current value with retry logic."""
        current_time = time.time()
        if self._last_update and current_time - self._last_update < MIN_UPDATE_INTERVAL:
            return

        async with self._update_lock:
            for attempt in range(MAX_RETRIES):
                try:
                    session = await self._get_session()
                    async with session.post(
                        f"http://{self._host}/main",
                        auth=aiohttp.BasicAuth(self._username, self._password),
                        timeout=UPDATE_TIMEOUT,
                    ) as response:
                        response.raise_for_status()
                        data = await response.json()
                        if "currentSet" in data:
                            self._value = min(
                                self._attr_native_max_value,
                                max(self._attr_native_min_value, float(data["currentSet"]))
                            )
                        self._attr_available = True
                        self._error_count = 0
                        self._last_update = current_time
                        return

                except aiohttp.ClientError as err:
                    if "Server disconnected" in str(err) or "Connection reset by peer" in str(err):
                        if attempt + 1 < MAX_RETRIES:
                            await asyncio.sleep(RETRY_DELAY * (2 ** attempt))  # Exponential backoff
                            continue
                    self._error_count += 1
                    self._attr_available = False if self._error_count >= self._max_errors else True
                    _LOGGER.error("Error updating charging current: %s", str(err))

                except Exception as err:
                    self._error_count += 1
                    self._attr_available = False if self._error_count >= self._max_errors else True
                    _LOGGER.error("Unexpected error updating charging current: %s", str(err))
                    return

    async def async_will_remove_from_hass(self) -> None:
        """Clean up resources."""
        if self._session and not self._session.closed:
            await self._session.close()
