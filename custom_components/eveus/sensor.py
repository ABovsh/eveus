"""Support for Eveus sensors."""
from __future__ import annotations

import logging
import asyncio
import aiohttp
import async_timeout

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfElectricPotential, 
    CONF_HOST, 
    CONF_USERNAME, 
    CONF_PASSWORD,
)
from homeassistant.helpers.typing import StateType

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Eveus sensors."""
    host = config_entry.data[CONF_HOST]
    username = config_entry.data[CONF_USERNAME]
    password = config_entry.data[CONF_PASSWORD]

    updater = EveusUpdater(host, username, password, hass)
    sensors = [
        EveusVoltageSensor(updater),
        EveusSystemTimeSensor(updater)
    ]
    async_add_entities(sensors, True)

class EveusUpdater:
    """Class to handle Eveus data updates."""

    def __init__(self, host: str, username: str, password: str, hass: HomeAssistant) -> None:
        """Initialize the updater."""
        self._host = host
        self._username = username
        self._password = password
        self._hass = hass
        self._data = {}
        self._available = True
        self._update_task = None
        self._sensors = []

    def register_sensor(self, sensor: "BaseEveusSensor") -> None:
        """Register a sensor to update."""
        self._sensors.append(sensor)

    async def start_updates(self) -> None:
        """Start the update loop."""
        async def update_loop(now=None):
            """Handle each update tick."""
            while True:
                try:
                    await self._update()
                    # Update all registered sensors
                    for sensor in self._sensors:
                        sensor.async_write_ha_state()
                except Exception as err:
                    _LOGGER.error("Error updating Eveus data: %s", err)
                await asyncio.sleep(10)  # Update every 10 seconds

        self._update_task = self._hass.loop.create_task(update_loop())

    async def _update(self) -> None:
        """Fetch new state data for the sensors."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"http://{self._host}/main",
                    auth=aiohttp.BasicAuth(self._username, self._password),
                    timeout=10
                ) as response:
                    response.raise_for_status()
                    self._data = await response.json()
                    self._available = True
                    _LOGGER.debug("Data updated - Voltage: %s, System Time: %s", 
                                self._data.get("voltMeas1"), 
                                self._data.get("systemTime"))
        except Exception as err:
            self._available = False
            raise

    @property
    def data(self) -> dict:
        """Return the current data."""
        return self._data

    @property
    def available(self) -> bool:
        """Return if updater is available."""
        return self._available

class BaseEveusSensor(SensorEntity):
    """Base implementation for Eveus sensor."""

    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the sensor."""
        self._updater = updater
        self._updater.register_sensor(self)
        self._attr_unique_id = f"{updater._host}_{self.entity_key}"
        self._attr_name = f"eveus_{self.entity_name}"

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()
        await self._updater.start_updates()

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._updater.available

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self._updater._host)},
            "name": "Eveus EV Charger",
            "manufacturer": "Eveus",
        }

class EveusVoltageSensor(BaseEveusSensor):
    """Implementation of Eveus voltage sensor."""

    entity_key = "voltage"
    entity_name = "voltage"
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        try:
            return float(self._updater.data["voltMeas1"])
        except (KeyError, TypeError, ValueError):
            return None

class EveusSystemTimeSensor(BaseEveusSensor):
    """Implementation of Eveus system time sensor."""

    entity_key = "system_time"
    entity_name = "system_time"

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        try:
            return int(self._updater.data["systemTime"])
        except (KeyError, TypeError, ValueError):
            return None
