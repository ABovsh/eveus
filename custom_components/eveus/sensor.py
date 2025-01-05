"""Support for Eveus sensors."""
from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

import aiohttp
import async_timeout
import voluptuous as vol

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
    UnitOfTime,
    PERCENTAGE,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

STATE_MAPPING = {
    0: 'Startup',
    1: 'System Test',
    2: 'Standby',
    3: 'Connected',
    4: 'Charging',
    5: 'Charge Complete',
    6: 'Paused',
    7: 'Error'
}

ERROR_STATES = {
    0: 'No Error',
    1: 'Grounding Error',
    2: 'Current Leak High',
    3: 'Relay Error',
    4: 'Current Leak Low',
    5: 'Box Overheat',
    6: 'Plug Overheat',
    7: 'Pilot Error',
    8: 'Low Voltage',
    9: 'Diode Error',
    10: 'Overcurrent',
    11: 'Interface Timeout',
    12: 'Software Failure',
    13: 'GFCI Test Failure',
    14: 'High Voltage'
}

NORMAL_SUBSTATES = {
    0: 'No Limits',
    1: 'Limited by User',
    2: 'Energy Limit',
    3: 'Time Limit',
    4: 'Cost Limit',
    5: 'Schedule 1 Limit',
    6: 'Schedule 1 Energy Limit',
    7: 'Schedule 2 Limit',
    8: 'Schedule 2 Energy Limit',
    9: 'Waiting for Activation',
    10: 'Paused by Adaptive Mode'
}

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Eveus sensors."""
    coordinator = EveusDataUpdateCoordinator(hass, config_entry)
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
            name="voltage",
            key="voltMeas1",
            device_class=SensorDeviceClass.VOLTAGE,
            native_unit_of_measurement=UnitOfElectricPotential.VOLT,
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
            name="power",
            key="powerMeas",
            device_class=SensorDeviceClass.POWER,
            native_unit_of_measurement=UnitOfPower.WATT,
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
        EveusStateSensor(coordinator=coordinator),
        EveusSubstateSensor(coordinator=coordinator),
        EveusSessionTimeSensor(coordinator=coordinator),
        EveusEnabledSensor(coordinator=coordinator),
        EveusGroundSensor(coordinator=coordinator),
        EveusCounterSensor(
            coordinator=coordinator,
            name="counter_a_energy",
            key="IEM1",
            device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        ),
        EveusCounterSensor(
            coordinator=coordinator,
            name="counter_b_energy",
            key="IEM2",
            device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        ),
        EveusCounterSensor(
            coordinator=coordinator,
            name="counter_a_cost",
            key="IEM1_money",
            device_class=None,
            native_unit_of_measurement="₴",
        ),
        EveusCounterSensor(
            coordinator=coordinator,
            name="counter_b_cost",
            key="IEM2_money",
            device_class=None,
            native_unit_of_measurement="₴",
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
        try:
            async with async_timeout.timeout(10):
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"http://{self.host}/main",
                        auth=aiohttp.BasicAuth(self.username, self.password),
                    ) as response:
                        response.raise_for_status()
                        return await response.json()
        except Exception as err:
            _LOGGER.error("Error while fetching data: %s", err)
            raise

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
    """Implementation of Eveus state sensor."""

    def __init__(self, coordinator: EveusDataUpdateCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_name = "eveus_state"
        self._attr_unique_id = f"{coordinator.host}_state"

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        try:
            state_num = self.coordinator.data["state"]
            return STATE_MAPPING.get(state_num, "Unknown")
        except (KeyError, TypeError):
            return "Unknown"

class EveusSubstateSensor(CoordinatorEntity, SensorEntity):
    """Implementation of Eveus substate sensor."""

    def __init__(self, coordinator: EveusDataUpdateCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_name = "eveus_substate"
        self._attr_unique_id = f"{coordinator.host}_substate"

    @property
    def native_value(self) -> str:
        """Return the substate of the sensor."""
        try:
            state_num = self.coordinator.data["state"]
            substate_num = self.coordinator.data["subState"]
            
            if state_num == 7:  # Error state
                return ERROR_STATES.get(substate_num, "Unknown Error")
            return NORMAL_SUBSTATES.get(substate_num, "Unknown State")
        except (KeyError, TypeError):
            return "Unknown"

class EveusSessionTimeSensor(CoordinatorEntity, SensorEntity):
    """Implementation of Eveus session time sensor."""

    def __init__(self, coordinator: EveusDataUpdateCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_name = "eveus_session_duration"
        self._attr_unique_id = f"{coordinator.host}_session_duration"
        self._attr_device_class = SensorDeviceClass.DURATION
        self._attr_native_unit_of_measurement = UnitOfTime.SECONDS

    @property
    def native_value(self) -> str:
        """Return formatted session time."""
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
                
            return " ".join(parts) if parts else "0m"
        except (KeyError, TypeError, ValueError):
            return "0m"

class EveusEnabledSensor(CoordinatorEntity, SensorEntity):
    """Implementation of Eveus enabled sensor."""

    def __init__(self, coordinator: EveusDataUpdateCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_name = "eveus_enabled"
        self._attr_unique_id = f"{coordinator.host}_enabled"

    @property
    def native_value(self) -> str:
        """Return if Eveus is enabled."""
        try:
            return "Yes" if self.coordinator.data["evseEnabled"] == 1 else "No"
        except (KeyError, TypeError):
            return "Unknown"

class EveusGroundSensor(CoordinatorEntity, SensorEntity):
    """Implementation of Eveus ground sensor."""

    def __init__(self, coordinator: EveusDataUpdateCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_name = "eveus_ground"
        self._attr_unique_id = f"{coordinator.host}_ground"

    @property
    def native_value(self) -> str:
        """Return ground status."""
        try:
            return "Yes" if self.coordinator.data["ground"] == 1 else "No"
        except (KeyError, TypeError):
            return "Unknown"

class EveusCounterSensor(CoordinatorEntity, SensorEntity):
    """Implementation of Eveus counter sensor."""

    def __init__(
        self,
        coordinator: EveusDataUpdateCoordinator,
        name: str,
        key: str,
        device_class: SensorDeviceClass | None = None,
        native_unit_of_measurement: str | None = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_name = f"eveus_{name}"
        self._attr_unique_id = f"{coordinator.host}_{name}"
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = native_unit_of_measurement
        self._key = key

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        try:
            value = self.coordinator.data[self._key]
            return round(float(value), 2) if value is not None else None
        except (KeyError, TypeError, ValueError):
            return None
