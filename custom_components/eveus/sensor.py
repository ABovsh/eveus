from __future__ import annotations

import logging
from typing import Any

import aiohttp
import async_timeout
import json

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
    UnitOfEnergy,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Eveus sensor."""
    coordinator = EveusDataUpdateCoordinator(hass, config_entry)
    await coordinator.async_config_entry_first_refresh()

    entities = [
        EveusSensor(
            coordinator=coordinator,
            name="Current",
            key="curMeas1",
            device_class=SensorDeviceClass.CURRENT,
            native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
            state_class=SensorStateClass.MEASUREMENT,
        ),
        EveusSensor(
            coordinator=coordinator,
            name="Voltage",
            key="voltMeas1",
            device_class=SensorDeviceClass.VOLTAGE,
            native_unit_of_measurement=UnitOfElectricPotential.VOLT,
            state_class=SensorStateClass.MEASUREMENT,
        ),
        EveusSensor(
            coordinator=coordinator,
            name="Power",
            key="powerMeas",
            device_class=SensorDeviceClass.POWER,
            native_unit_of_measurement=UnitOfPower.WATT,
            state_class=SensorStateClass.MEASUREMENT,
        ),
        EveusSensor(
            coordinator=coordinator,
            name="Session Energy",
            key="sessionEnergy",
            device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            state_class=SensorStateClass.TOTAL_INCREASING,
        ),
        EveusSensor(
            coordinator=coordinator,
            name="Box Temperature",
            key="temperature1",
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            state_class=SensorStateClass.MEASUREMENT,
        ),
    ]

    async_add_entities(entities)

class EveusDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Eveus data."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.host = config_entry.data["host"]
        self.username = config_entry.data["username"]
        self.password = config_entry.data["password"]

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Eveus."""
        async with async_timeout.timeout(10):
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"http://{self.host}/main",
                    auth=aiohttp.BasicAuth(self.username, self.password),
                ) as response:
                    response.raise_for_status()
                    return await response.json()

class EveusSensor(CoordinatorEntity, SensorEntity):
    """Representation of an Eveus sensor."""

    def __init__(
        self,
        coordinator: EveusDataUpdateCoordinator,
        name: str,
        key: str,
        device_class: SensorDeviceClass | None = None,
        native_unit_of_measurement: str | None = None,
        state_class: SensorStateClass | None = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_name = name
        self._attr_unique_id = f"{coordinator.host}_{key}"
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = native_unit_of_measurement
        self._attr_state_class = state_class
        self._key = key

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        try:
            return self.coordinator.data[self._key]
        except KeyError:
            return None
