"""Support for Eveus sensors."""
from __future__ import annotations

import logging
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
from homeassistant.core import HomeAssistant
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
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.host = config_entry.data[CONF_HOST]
        self.username = config_entry.data[CONF_USERNAME]
        self.password = config_entry.data[CONF_PASSWORD]
        LOGGER.debug("Coordinator initialized for %s with interval %s", self.host, SCAN_INTERVAL)

    async def _async_update_data(self) -> dict:
        """Fetch data from API endpoint."""
        LOGGER.debug("Starting data update for %s", self.host)
        try:
            async with async_timeout.timeout(10):
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"http://{self.host}/main",
                        auth=aiohttp.BasicAuth(self.username, self.password),
                    ) as response:
                        response.raise_for_status()
                        data = await response.json()
                        LOGGER.debug(
                            "Data received - Voltage: %sV",
                            data.get("voltMeas1", "unknown"),
                        )
                        return data
        except Exception as err:
            LOGGER.error("Error updating data: %s", err)
            raise

async def async_setup_entry(
    hass: HomeAssistant, 
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eveus sensor based on a config entry."""
    LOGGER.debug("Setting up Eveus voltage sensor")
    
    coordinator = EveusDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    
    async_add_entities([
        EveusVoltageSensor(coordinator)
    ])

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

    @property
    def native_value(self):
        """Return the voltage value."""
        try:
            return float(self.coordinator.data["voltMeas1"])
        except (KeyError, TypeError, ValueError):
            return None

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self.coordinator.host)},
            "name": "Eveus EV Charger",
            "manufacturer": "Eveus",
        }
