"""Support for Eveus sensors."""
from __future__ import annotations
import logging
import asyncio
import async_timeout
import aiohttp
import voluptuous as vol
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfElectricPotential, CONF_HOST, CONF_USERNAME, CONF_PASSWORD
from homeassistant.helpers.typing import StateType

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Eveus sensor."""
    host = config_entry.data[CONF_HOST]
    username = config_entry.data[CONF_USERNAME]
    password = config_entry.data[CONF_PASSWORD]

    sensor = EveusVoltageSensor(host, username, password, hass)
    async_add_entities([sensor], True)

class EveusVoltageSensor(SensorEntity):
    """Representation of an Eveus voltage sensor."""

    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, host: str, username: str, password: str, hass: HomeAssistant) -> None:
        """Initialize the sensor."""
        self._host = host
        self._username = username
        self._password = password
        self._attr_name = "eveus_voltage"
        self._attr_unique_id = f"{host}_voltage"
        self._state = None
        self._available = True
        self._hass = hass
        self._update_task = None
        _LOGGER.debug("Voltage sensor initialized")

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()
        
        async def update_loop(now=None):
            """Update the sensor state periodically."""
            try:
                while True:
                    _LOGGER.debug("Starting update cycle")
                    await self._update()
                    _LOGGER.debug("Update complete, voltage: %s", self._state)
                    # Write to HA state machine
                    self.async_write_ha_state()
                    await asyncio.sleep(10)  # Update every 10 seconds
            except Exception as e:
                _LOGGER.error("Error in update loop: %s", e)

        # Start the update loop
        self._update_task = self._hass.loop.create_task(update_loop())

    async def async_will_remove_from_hass(self) -> None:
        """Handle entity removal."""
        if self._update_task:
            self._update_task.cancel()

    async def _update(self) -> None:
        """Update the sensor state."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"http://{self._host}/main",
                    auth=aiohttp.BasicAuth(self._username, self._password),
                    timeout=10
                ) as response:
                    response.raise_for_status()
                    data = await response.json()
                    self._state = float(data["voltMeas1"])
                    self._available = True
                    _LOGGER.debug("Successfully updated voltage: %s", self._state)
        except Exception as err:
            self._available = False
            _LOGGER.error("Failed to update: %s", err)
            raise

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        return self._state

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._available

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self._host)},
            "name": "Eveus EV Charger",
            "manufacturer": "Eveus",
        }
