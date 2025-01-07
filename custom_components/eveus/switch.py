"""Support for Eveus switches."""
from __future__ import annotations

import logging
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
        """Get or create client session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _send_command(self, command: str, value: int) -> bool:
        """Send command to the device."""
        try:
            session = await self._get_session()
            async with session.post(
                f"http://{self._host}/pageEvent",
                auth=aiohttp.BasicAuth(self._username, self._password),
                headers={"Content-type": "application/x-www-form-urlencoded"},
                data=f"pageevent={command}&{command}={value}",
                timeout=10,
            ) as response:
                response.raise_for_status()
                self._available = True
                _LOGGER.debug(
                    "Successfully sent command %s=%s to %s",
                    command,
                    value,
                    self._host,
                )
                return True
        except aiohttp.ClientResponseError as error:
            self._available = False
            _LOGGER.error(
                "HTTP error sending command to %s: %s [status=%s]",
                self._host,
                error.message,
                error.status,
            )
            return False
        except aiohttp.ClientError as error:
            self._available = False
            _LOGGER.error(
                "Connection error sending command to %s: %s",
                self._host,
                str(error),
            )
            return False
        except Exception as error:
            self._available = False
            _LOGGER.error(
                "Unexpected error sending command to %s: %s",
                self._host,
                str(error),
            )
            return False

    async def _get_state(self, attribute: str) -> bool | None:
        """Get state from the device."""
        try:
            session = await self._get_session()
            async with session.post(
                f"http://{self._host}/main",
                auth=aiohttp.BasicAuth(self._username, self._password),
                timeout=10,
            ) as response:
                response.raise_for_status()
                data = await response.json()
                self._available = True
                return data.get(attribute) == 1
        except Exception as error:
            self._available = False
            _LOGGER.error(
                "Failed to get state from %s: %s",
                self._host,
                error,
            )
            return None

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
            self._is_on = False

    async def async_turn_off(self, **kwargs: Any) -> None:
        """No-op for reset switch."""
        self._is_on = False

    async def async_update(self) -> None:
        """Update state."""
        self._is_on = False
