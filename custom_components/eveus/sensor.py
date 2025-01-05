"""Support for Eveus sensors."""
from __future__ import annotations

import logging
import asyncio
import aiohttp
import async_timeout

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_USERNAME,
    CONF_PASSWORD,
    UnitOfElectricPotential,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN, LOGGER, SCAN_INTERVAL

class EveusDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Eveus data."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize coordinator."""
        print(">>> COORDINATOR INIT START") # Print statement for visibility
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.hass = hass
        self.host = config_entry.data[CONF_HOST]
        self.username = config_entry.data[CONF_USERNAME]
        self.password = config_entry.data[CONF_PASSWORD]
        self._update_task = None
        print(f">>> COORDINATOR INIT COMPLETE for {self.host} with interval {SCAN_INTERVAL}") # Print statement

    async def _async_update_data(self) -> dict:
        """Fetch data from API endpoint."""
        print(f">>> UPDATE STARTED for {self.host}") # Print statement
        try:
            async with async_timeout.timeout(10):
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"http://{self.host}/main",
                        auth=aiohttp.BasicAuth(self.username, self.password),
                    ) as response:
                        response.raise_for_status()
                        data = await response.json()
                        print(f">>> DATA RECEIVED - Voltage: {data.get('voltMeas1', 'unknown')}V") # Print statement
                        return data
        except Exception as err:
            print(f">>> ERROR UPDATING: {err}") # Print statement
            raise

    async def start_updates(self):
        """Start the update loop."""
        print(">>> STARTING UPDATE LOOP") # Print statement
        if self._update_task:
            self._update_task.cancel()
        
        async def update_loop():
            """Update loop."""
            while True:
                print(">>> UPDATE LOOP ITERATION") # Print statement
                try:
                    await self.async_refresh()
                except Exception as err:
                    print(f">>> UPDATE LOOP ERROR: {err}") # Print statement
                await asyncio.sleep(SCAN_INTERVAL.total_seconds())

        self._update_task = asyncio.create_task(update_loop())

async def async_setup_entry(
    hass: HomeAssistant, 
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eveus sensor based on a config entry."""
    print(">>> SETUP ENTRY START") # Print statement
    
    coordinator = EveusDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    
    # Start the update loop
    await coordinator.start_updates()
    
    entities = [EveusVoltageSensor(coordinator)]
    async_add_entities(entities)
    print(">>> SETUP ENTRY COMPLETE") # Print statement

class EveusVoltageSensor(CoordinatorEntity, SensorEntity):
    """Implementation of Eveus voltage sensor."""

    def __init__(self, coordinator: EveusDataUpdateCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_name = "eveus_voltage"
        self._attr_unique_id = f"{coordinator.host}_voltage"
        self._attr_device_class = SensorDeviceClass.VOLTAGE
        self._attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
        self._attr_state_class = SensorStateClass.MEASUREMENT
        print(f">>> VOLTAGE SENSOR INITIALIZED: {self._attr_unique_id}") # Print statement

    @property
    def native_value(self):
        """Return the voltage value."""
        try:
            value = float(self.coordinator.data["voltMeas1"])
            print(f">>> VOLTAGE VALUE READ: {value}V") # Print statement
            return value
        except (KeyError, TypeError, ValueError) as err:
            print(f">>> ERROR READING VOLTAGE: {err}") # Print statement
            return None

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self.coordinator.host)},
            "name": "Eveus EV Charger",
            "manufacturer": "Eveus",
        }
