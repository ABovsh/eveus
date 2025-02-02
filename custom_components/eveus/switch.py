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

    # Initialize entities dict if needed
    if "entities" not in hass.data[DOMAIN][entry.entry_id]:
        hass.data[DOMAIN][entry.entry_id]["entities"] = {}

    # Store switch references with unique_id as key
    hass.data[DOMAIN][entry.entry_id]["entities"]["switch"] = {
        switch.unique_id: switch for switch in switches
    }

    async_add_entities(switches)

class BaseEveusSwitch(SwitchEntity):
    """Base class for Eveus switches."""

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
        self._last_update = time.time()
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
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create client session with proper configuration."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10, connect=5)
            connector = aiohttp.TCPConnector(limit=1, force_close=True)
            self._session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return self._session

    async def _send_command(self, command: str, value: int, verify_command: bool = True) -> bool:
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
                        response_text = await response.text()
                        
                        if "error" in response_text.lower():
                            raise ValueError(f"Error in response: {response_text}")

                        if verify_command:
                            # Verify command via main endpoint
                            async with session.post(
                                f"http://{self._host}/main",
                                auth=aiohttp.BasicAuth(self._username, self._password),
                                timeout=COMMAND_TIMEOUT,
                            ) as verify_response:
                                verify_response.raise_for_status()
                                verify_data = await verify_response.json()
                                if not self._validate_command_response(verify_data, command, value):
                                    raise ValueError("Command verification failed")

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

                except aiohttp.ClientError as err:
                    if "Connection reset by peer" in str(err) or "Server disconnected" in str(err):
                        if attempt + 1 < MAX_RETRIES:
                            await asyncio.sleep(RETRY_DELAY * (2 ** attempt))
                            continue
                    raise

                except Exception as error:
                    if attempt + 1 >= MAX_RETRIES:
                        self._error_count += 1
                        self._available = False if self._error_count >= self._max_errors else True
                        _LOGGER.error(
                            "Failed to send command after %d attempts to %s: %s",
                            MAX_RETRIES,
                            self._host,
                            str(error),
                        )
                        return False
                    await asyncio.sleep(RETRY_DELAY * (attempt + 1))

            return False

    def _validate_command_response(self, response_data: dict, command: str, value: int) -> bool:
        """Validate command response data."""
        if not isinstance(response_data, dict):
            return False

        try:
            if command == "evseEnabled":
                return response_data.get("evseEnabled") == value
            elif command == "oneCharge":
                return response_data.get("oneCharge") == value
            elif command == "rstEM1":
                return True  # Reset commands don't need validation
        except Exception as err:
            _LOGGER.debug("Validation error for command %s: %s", command, str(err))
            return False

        return False

    async def async_update(self) -> None:
        """Update device state with retries."""
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
                        self._state_data = await response.json()
                        self._available = True
                        self._error_count = 0
                        self._last_update = current_time
                        return

                except aiohttp.ClientError as err:
                    if "Connection reset by peer" in str(err) or "Server disconnected" in str(err):
                        if attempt + 1 < MAX_RETRIES:
                            await asyncio.sleep(RETRY_DELAY * (2 ** attempt))
                            continue
                    self._error_count += 1
                    self._available = False if self._error_count >= self._max_errors else True
                    _LOGGER.error("Error updating state for %s: %s", self.name, str(err))
                    break

                except Exception as err:
                    self._error_count += 1
                    self._available = False if self._error_count >= self._max_errors else True
                    _LOGGER.error("Unexpected error updating state for %s: %s", self.name, str(err))
                    break

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
        # Match the exact command format from working YAML implementation
        try:
            session = await self._get_session()
            async with session.post(
                f"http://{self._host}/pageEvent",
                auth=aiohttp.BasicAuth(self._username, self._password),
                headers={"Content-type": "application/x-www-form-urlencoded"},
                data="pageevent=rstEM1&rstEM1=0",
                timeout=COMMAND_TIMEOUT,
            ) as response:
                response.raise_for_status()
                self._is_on = False  # Always false as it's a momentary switch
        except Exception as error:
            _LOGGER.error("Failed to reset counter: %s", str(error))
            self._available = False

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Reset command for off state - matches on command for consistency."""
        try:
            session = await self._get_session()
            async with session.post(
                f"http://{self._host}/pageEvent",
                auth=aiohttp.BasicAuth(self._username, self._password),
                headers={"Content-type": "application/x-www-form-urlencoded"},
                data="pageevent=rstEM1&rstEM1=0",
                timeout=COMMAND_TIMEOUT,
            ) as response:
                response.raise_for_status()
                self._is_on = False
        except Exception as error:
            _LOGGER.error("Failed to reset counter: %s", str(error))
            self._available = False

    async def async_update(self) -> None:
        """Update state."""
        await super().async_update()
        if self._available:
            try:
                iem1_value = self._state_data.get("IEM1")
                if iem1_value in (None, "null", "", "undefined", "ERROR"):
                    self._is_on = False
                else:
                    # Convert to float and check if non-zero
                    self._is_on = float(iem1_value) != 0
            except (TypeError, ValueError):
                self._is_on = False
