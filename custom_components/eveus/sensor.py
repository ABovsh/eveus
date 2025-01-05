"""Support for Eveus sensors."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import aiohttp
import asyncio
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
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
    UnitOfEnergy,
    UnitOfTemperature,
    UnitOfTime,
    PERCENTAGE,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
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
        LOGGER.debug(
            "Initialized coordinator for %s with update interval %s",
            self.host,
            SCAN_INTERVAL,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API endpoint."""
        LOGGER.debug("Starting data update for %s", self.host)
        try:
            async with async_timeout.timeout(10):
                async with aiohttp.ClientSession() as session:
                    LOGGER.debug("Making request to %s", self.host)
                    async with session.post(
                        f"http://{self.host}/main",
                        auth=aiohttp.BasicAuth(self.username, self.password),
                        timeout=10,
                    ) as response:
                        if response.status == 401:
                            raise ConfigEntryAuthFailed(
                                "Authentication failed, please check credentials"
                            )
                        response.raise_for_status()
                        data = await response.json()
                        LOGGER.debug(
                            "Received data from %s. State: %s",
                            self.host,
                            data.get("state", "unknown"),
                        )
                        return data

        except asyncio.TimeoutError as error:
            LOGGER.error("Timeout fetching data from %s: %s", self.host, error)
            raise UpdateFailed(f"Timeout error: {error}") from error
        except aiohttp.ClientResponseError as error:
            LOGGER.error("Error fetching data from %s: %s", self.host, error)
            raise UpdateFailed(f"Error fetching data: {error}") from error
        except Exception as error:
            LOGGER.error("Unexpected error for %s: %s", self.host, error)
            raise UpdateFailed(f"Unexpected error: {error}") from error

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Eveus sensor based on a config entry."""
    LOGGER.debug("Setting up Eveus sensors for %s", entry.data[CONF_HOST])

    coordinator = EveusDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    entities = [
        EveusSensor(
            coordinator=coordinator,
            name="current_set",
            key="currentSet",
            device_class=SensorDeviceClass.CURRENT,
            native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
            state_class=SensorStateClass.MEASUREMENT,
        ),
        EveusSensor(
            coordinator=coordinator,
            name="current",
            key="curMeas1",
            device_class=SensorDeviceClass.CURRENT,
            native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
            state_class=SensorStateClass.MEASUREMENT,
        ),
        EveusSensor(
            coordinator=coordinator,
            name="voltage",
            key="voltMeas1",
            device_class=SensorDeviceClass.VOLTAGE,
            native_unit_of_measurement=UnitOfElectricPotential.VOLT,
            state_class=SensorStateClass.MEASUREMENT,
        ),
        EveusSensor(
            coordinator=coordinator,
            name="power",
            key="powerMeas",
            device_class=SensorDeviceClass.POWER,
            native_unit_of_measurement=UnitOfPower.WATT,
            state_class=SensorStateClass.MEASUREMENT,
        ),
        EveusSensor(
            coordinator=coordinator,
            name="box_temperature",
            key="temperature1",
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            state_class=SensorStateClass.MEASUREMENT,
        ),
        EveusSensor(
            coordinator=coordinator,
            name="plug_temperature",
            key="temperature2",
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            state_class=SensorStateClass.MEASUREMENT,
        ),
        EveusSensor(
            coordinator=coordinator,
            name="session_energy",
            key="sessionEnergy",
            device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            state_class=SensorStateClass.TOTAL,
        ),
        EveusSensor(
            coordinator=coordinator,
            name="total_energy",
            key="totalEnergy",
            device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            state_class=SensorStateClass.TOTAL_INCREASING,
        ),
        EveusSensor(
            coordinator=coordinator,
            name="counter_a_energy",
            key="IEM1",
            device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            state_class=SensorStateClass.TOTAL_INCREASING,
        ),
        EveusSensor(
            coordinator=coordinator,
            name="counter_b_energy",
            key="IEM2",
            device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            state_class=SensorStateClass.TOTAL_INCREASING,
        ),
        EveusSensor(
            coordinator=coordinator,
            name="counter_a_cost",
            key="IEM1_money",
            native_unit_of_measurement="₴",
            state_class=SensorStateClass.TOTAL_INCREASING,
        ),
        EveusSensor(
            coordinator=coordinator,
            name="counter_b_cost",
            key="IEM2_money",
            native_unit_of_measurement="₴",
            state_class=SensorStateClass.TOTAL_INCREASING,
        ),
        EveusStateSensor(coordinator),
        EveusSubstateSensor(coordinator),
        EveusSessionTimeSensor(coordinator),
        EveusEnabledSensor(coordinator),
    ]

    async_add_entities(entities)

class EveusSensor(CoordinatorEntity, SensorEntity):
    """Implementation of an Eveus sensor."""

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
        self._attr_name = f"eveus_{name}"
        self._attr_unique_id = f"{coordinator.host}_{name}"
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = native_unit_of_measurement
        self._attr_state_class = state_class
        self._key = key
        self._attr_has_entity_name = True

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self.coordinator.host)},
            "name": "Eveus EV Charger",
            "manufacturer": "Eveus",
            "model": self.coordinator.data.get("typeEvse", "Unknown"),
            "sw_version": self.coordinator.data.get("verFWMain", "Unknown"),
        }

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        try:
            value = self.coordinator.data[self._key]
            if isinstance(value, (int, float)):
                return round(float(value), 2)
            return value
        except (KeyError, TypeError, ValueError):
            return None

class EveusStateSensor(CoordinatorEntity, SensorEntity):
    """Representation of Eveus state sensor."""

    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator: EveusDataUpdateCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_name = "eveus_state"
        self._attr_unique_id = f"{coordinator.host}_state"
        self._attr_has_entity_name = True

    @property
    def native_value(self) -> str:
        """Return the state of the device."""
        state_map = {
            0: "Startup",
            1: "System Test",
            2: "Standby",
            3: "Connected",
            4: "Charging",
            5: "Charge Complete",
            6: "Paused",
            7: "Error",
        }
        try:
            return state_map.get(self.coordinator.data["state"], "Unknown")
        except (KeyError, TypeError):
            return "Unknown"

class EveusSubstateSensor(CoordinatorEntity, SensorEntity):
    """Representation of Eveus substate sensor."""

    def __init__(self, coordinator: EveusDataUpdateCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_name = "eveus_substate"
        self._attr_unique_id = f"{coordinator.host}_substate"
        self._attr_has_entity_name = True

    @property
    def native_value(self) -> str:
        """Return the substate of the device."""
        try:
            state = self.coordinator.data["state"]
            substate = self.coordinator.data["subState"]

            if state == 7:  # Error state
                error_states = {
                    0: "No Error",
                    1: "Grounding Error",
                    2: "Current Leak High",
                    3: "Relay Error",
                    4: "Current Leak Low",
                    5: "Box Overheat",
                    6: "Plug Overheat",
                    7: "Pilot Error",
                    8: "Low Voltage",
                    9: "Diode Error",
                    10: "Overcurrent",
                    11: "Interface Timeout",
                    12: "Software Failure",
                    13: "GFCI Test Failure",
                    14: "High Voltage",
                }
                return error_states.get(substate, "Unknown Error")
            else:
                normal_states = {
                    0: "No Limits",
                    1: "Limited by User",
                    2: "Energy Limit",
                    3: "Time Limit",
                    4: "Cost Limit",
                    5: "Schedule 1 Limit",
                    6: "Schedule 1 Energy Limit",
                    7: "Schedule 2 Limit",
                    8: "Schedule 2 Energy Limit",
                    9: "Waiting for Activation",
                    10: "Paused by Adaptive Mode",
                }
                return normal_states.get(substate, "Unknown State")
        except (KeyError, TypeError):
            return "Unknown"

class EveusSessionTimeSensor(CoordinatorEntity, SensorEntity):
    """Representation of Eveus session time sensor."""

    def __init__(self, coordinator: EveusDataUpdateCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_name = "eveus_session_time"
        self._attr_unique_id = f"{coordinator.host}_session_time"
        self._attr_device_class = SensorDeviceClass.DURATION
        self._attr_native_unit_of_measurement = UnitOfTime.SECONDS
        self._attr_has_entity_name = True
        self._attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self) -> int | None:
        """Return session time in seconds."""
        try:
            return int(self.coordinator.data["sessionTime"])
        except (KeyError, TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Return formatted time as an attribute."""
        try:
            seconds = int(self.coordinator.data["sessionTime"])
            days = seconds // 86400
            hours = (seconds % 86400) // 3600
            minutes = (seconds % 3600) // 60
            
            parts = []
            if days > 0:
                parts.append(f"{days}d")
            if hours > 0:
                parts.append(f"{hours}h")
            if minutes > 0:
                parts.append(f"{minutes}m")
                
            return {
                "formatted_duration": " ".join(parts) if parts else "0m"
            }
        except (KeyError, TypeError, ValueError):
            return {"formatted_duration": "Unknown"}

class EveusEnabledSensor(CoordinatorEntity, SensorEntity):
    """Representation of Eveus enabled sensor."""

    def __init__(self, coordinator: EveusDataUpdateCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_name = "eveus_enabled"
        self._attr_unique_id = f"{coordinator.host}_enabled"
        self._attr_has_entity_name = True

    @property
    def native_value(self) -> str:
        """Return if the device is enabled."""
        try:
            return "Yes" if self.coordinator.data["evseEnabled"] == 1 else "No"
        except (KeyError, TypeError):
            return "Unknown"
