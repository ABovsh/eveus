"""Support for Eveus sensors."""
from __future__ import annotations

import logging
import asyncio
import aiohttp
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    SCAN_INTERVAL,
    CHARGING_STATES,
    ERROR_STATES,
    NORMAL_SUBSTATES,
    ATTR_VOLTAGE,
    ATTR_CURRENT,
    ATTR_POWER,
    ATTR_SESSION_ENERGY,
    ATTR_TOTAL_ENERGY,
    ATTR_SESSION_TIME,
    ATTR_STATE,
    ATTR_SUBSTATE,
    ATTR_CURRENT_SET,
    ATTR_ENABLED,
    ATTR_TEMPERATURE_BOX,
    ATTR_TEMPERATURE_PLUG,
    ATTR_SYSTEM_TIME,
    ATTR_COUNTER_A_ENERGY,
    ATTR_COUNTER_B_ENERGY,
    ATTR_COUNTER_A_COST,
    ATTR_COUNTER_B_COST,
    ATTR_GROUND,
)

_LOGGER = logging.getLogger(__name__)

# Constants for error handling
MAX_FAILED_ATTEMPTS = 3
RETRY_DELAY = 30
DEFAULT_TIMEOUT = 10


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Eveus sensors."""
    _LOGGER.debug("Setting up Eveus sensors for %s", entry.data[CONF_HOST])

    coordinator = EveusDataUpdateCoordinator(
        hass, entry.data[CONF_HOST], entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]
    )
    await coordinator.async_config_entry_first_refresh()

    entities = [
        # Measurement sensors
        EveusVoltageSensor(coordinator),
        EveusCurrentSensor(coordinator),
        EveusPowerSensor(coordinator),
        EveusCurrentSetSensor(coordinator),
        
        # Energy sensors
        EveusSessionEnergySensor(coordinator),
        EveusTotalEnergySensor(coordinator),
        EveusCounterAEnergySensor(coordinator),
        EveusCounterBEnergySensor(coordinator),
        
        # Temperature sensors
        EveusBoxTemperatureSensor(coordinator),
        EveusPlugTemperatureSensor(coordinator),
        
        # Status sensors
        EveusStateSensor(coordinator),
        EveusSubstateSensor(coordinator),
        EveusEnabledSensor(coordinator),
        EveusGroundSensor(coordinator),
        
        # Time sensors
        EveusSystemTimeSensor(coordinator),
        EveusSessionTimeSensor(coordinator),
        
        # Cost sensors
        EveusCounterACostSensor(coordinator),
        EveusCounterBCostSensor(coordinator),
        
        # Battery sensors
        EveusBatteryVoltageSensor(coordinator),
        
        # SOC sensors
        EveusStateOfChargeKwhSensor(coordinator),
        EveusStateOfChargePercentSensor(coordinator),
        EveusTimeToTargetSensor(coordinator),
    ]

    async_add_entities(entities, True)


class EveusDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Eveus data."""

    def __init__(
        self, hass: HomeAssistant, host: str, username: str, password: str
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.host = host
        self.username = username
        self.password = password
        self._session = None

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Eveus."""
        if self._session is None:
            self._session = aiohttp.ClientSession()

        failed_attempts = 0
        while failed_attempts < MAX_FAILED_ATTEMPTS:
            try:
                async with self._session.post(
                    f"http://{self.host}/main",
                    auth=aiohttp.BasicAuth(self.username, self.password),
                    timeout=DEFAULT_TIMEOUT,
                ) as response:
                    response.raise_for_status()
                    data = await response.json()
                    _LOGGER.debug(
                        "Updated data from %s: State=%s, Power=%sW",
                        self.host,
                        data.get(ATTR_STATE),
                        data.get(ATTR_POWER),
                    )
                    return data

            except asyncio.TimeoutError:
                failed_attempts += 1
                _LOGGER.warning(
                    "Timeout fetching data from %s (attempt %d of %d)",
                    self.host,
                    failed_attempts,
                    MAX_FAILED_ATTEMPTS,
                )
                if failed_attempts == MAX_FAILED_ATTEMPTS:
                    raise
                await asyncio.sleep(RETRY_DELAY)

            except aiohttp.ClientError as err:
                failed_attempts += 1
                _LOGGER.error(
                    "Error fetching data from %s: %s (attempt %d of %d)",
                    self.host,
                    err,
                    failed_attempts,
                    MAX_FAILED_ATTEMPTS,
                )
                if failed_attempts == MAX_FAILED_ATTEMPTS:
                    raise
                await asyncio.sleep(RETRY_DELAY)

    async def async_shutdown(self) -> None:
        """Shutdown the coordinator."""
        if self._session:
            await self._session.close()
            self._session = None


class BaseEveusSensor(CoordinatorEntity, SensorEntity):
    """Base class for Eveus sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: EveusDataUpdateCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.host}_{self.entity_description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.host)},
            "name": "Eveus EV Charger",
            "manufacturer": "Eveus",
            "sw_version": coordinator.data.get("verFWMain", "Unknown"),
        }


class EveusVoltageSensor(BaseEveusSensor):
    """Implementation of the voltage sensor."""

    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_entity_description = SensorEntityDescription(
        key=ATTR_VOLTAGE,
        name="Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
    )

    @property
    def native_value(self) -> float | None:
        """Return the voltage."""
        try:
            return float(self.coordinator.data[ATTR_VOLTAGE])
        except (KeyError, TypeError, ValueError):
            return None


class EveusCurrentSensor(BaseEveusSensor):
    """Implementation of the current sensor."""

    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_entity_description = SensorEntityDescription(
        key=ATTR_CURRENT,
        name="Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-ac",
    )

    @property
    def native_value(self) -> float | None:
        """Return the current."""
        try:
            return float(self.coordinator.data[ATTR_CURRENT])
        except (KeyError, TypeError, ValueError):
            return None


class EveusPowerSensor(BaseEveusSensor):
    """Implementation of the power sensor."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_entity_description = SensorEntityDescription(
        key=ATTR_POWER,
        name="Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
    )

    @property
    def native_value(self) -> float | None:
        """Return the power consumption."""
        try:
            return float(self.coordinator.data[ATTR_POWER])
        except (KeyError, TypeError, ValueError):
            return None


class EveusEnergyBaseSensor(BaseEveusSensor):
    """Base implementation for energy sensors."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self) -> float | None:
        """Return the energy value."""
        try:
            return float(self.coordinator.data[self._attr_entity_description.key])
        except (KeyError, TypeError, ValueError):
            return None


class EveusSessionEnergySensor(EveusEnergyBaseSensor):
    """Implementation of session energy sensor."""

    _attr_entity_description = SensorEntityDescription(
        key=ATTR_SESSION_ENERGY,
        name="Session Energy",
        icon="mdi:battery-charging",
    )


class EveusTotalEnergySensor(EveusEnergyBaseSensor):
    """Implementation of total energy sensor."""

    _attr_entity_description = SensorEntityDescription(
        key=ATTR_TOTAL_ENERGY,
        name="Total Energy",
        icon="mdi:battery-charging",
    )


class EveusCounterAEnergySensor(EveusEnergyBaseSensor):
    """Implementation of counter A energy sensor."""

    _attr_entity_description = SensorEntityDescription(
        key=ATTR_COUNTER_A_ENERGY,
        name="Counter A Energy",
        icon="mdi:counter",
    )


class EveusCounterBEnergySensor(EveusEnergyBaseSensor):
    """Implementation of counter B energy sensor."""

    _attr_entity_description = SensorEntityDescription(
        key=ATTR_COUNTER_B_ENERGY,
        name="Counter B Energy",
        icon="mdi:counter",
    )


class EveusTemperatureBaseSensor(BaseEveusSensor):
    """Base implementation for temperature sensors."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        """Return the temperature value."""
        try:
            return float(self.coordinator.data[self._attr_entity_description.key])
        except (KeyError, TypeError, ValueError):
            return None


class EveusBoxTemperatureSensor(EveusTemperatureBaseSensor):
    """Implementation of box temperature sensor."""

    _attr_entity_description = SensorEntityDescription(
        key=ATTR_TEMPERATURE_BOX,
        name="Box Temperature",
        icon="mdi:thermometer",
    )


class EveusPlugTemperatureSensor(EveusTemperatureBaseSensor):
    """Implementation of plug temperature sensor."""

    _attr_entity_description = SensorEntityDescription(
        key=ATTR_TEMPERATURE_PLUG,
        name="Plug Temperature",
        icon="mdi:thermometer",
    )


class EveusStateSensor(BaseEveusSensor):
    """Implementation of state sensor."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_description = SensorEntityDescription(
        key=ATTR_STATE,
        name="State",
        icon="mdi:ev-station",
    )

    @property
    def native_value(self) -> str:
        """Return the charging state."""
        try:
            state = self.coordinator.data[ATTR_STATE]
            return CHARGING_STATES.get(state, "Unknown")
        except (KeyError, TypeError):
            return "Unknown"


class EveusSubstateSensor(BaseEveusSensor):
    """Implementation of substate sensor."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_description = SensorEntityDescription(
        key=ATTR_SUBSTATE,
        name="Substate",
        icon="mdi:information",
    )

    @property
    def native_value(self) -> str:
        """Return the substate."""
        try:
            state = self.coordinator.data[ATTR_STATE]
            substate = self.coordinator.data[ATTR_SUBSTATE]
            if state == 7:  # Error state
                return ERROR_STATES.get(substate, "Unknown Error")
            return NORMAL_SUBSTATES.get(substate, "Unknown State")
        except (KeyError, TypeError):
            return "Unknown"


class EveusEnabledSensor(BaseEveusSensor):
    """Implementation of enabled sensor."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_description = SensorEntityDescription(
        key=ATTR_ENABLED,
        name="Enabled",
        icon="mdi:power",
    )

    @property
    def native_value(self) -> str:
        """Return if charging is enabled."""
        try:
            return "Yes" if self.coordinator.data[ATTR_ENABLED] == 1 else "No"
        except (KeyError, TypeError):
            return "Unknown"


class EveusGroundSensor(BaseEveusSensor):
    """Implementation of ground sensor."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_description = SensorEntityDescription(
        key=ATTR_GROUND,
        name="Ground",
        icon="mdi:ground",
    )

    @property
    def native_value(self) -> str:
        """Return ground status."""
        try:
            return "Yes" if self.coordinator.data[ATTR_GROUND] == 1 else "No"
        except (KeyError, TypeError):
            return "Unknown"


class EveusSystemTimeSensor(BaseEveusSensor):
    """Implementation of system time sensor."""

    _attr_entity_description = SensorEntityDescription(
        key=ATTR_SYSTEM_TIME,
        name="System Time",
        icon="mdi:clock",
    )

    @property
    def native_value(self) -> int | None:
        """Return system time."""
        try:
            return int(self.coordinator.data[ATTR_SYSTEM_TIME])
        except (KeyError, TypeError, ValueError):
            return None


class EveusSessionTimeSensor(BaseEveusSensor):
    """Implementation of session time sensor."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_description = SensorEntityDescription(
        key=ATTR_SESSION_TIME,
        name="Session Time",
        icon="mdi:timer",
    )

    @property
    def native_value(self) -> str | None:
        """Return formatted session time."""
        try:
            seconds = int(self.coordinator.data[ATTR_SESSION_TIME])
            if seconds == 0:
                return "0m"

            days = seconds // 86400
            hours = (seconds % 86400) // 3600
            minutes = (seconds % 3600) // 60
            
            parts = []
            if days > 0:
                parts.append(f"{days}d")
            if hours > 0:
                parts.append(f"{hours}h")
            if minutes > 0 or not parts:  # Show minutes if it's the only non-zero value
                parts.append(f"{minutes}m")
                
            return " ".join(parts)
        except (KeyError, TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        attrs = super().extra_state_attributes or {}
        try:
            attrs["raw_seconds"] = int(self.coordinator.data[ATTR_SESSION_TIME])
        except (KeyError, TypeError, ValueError):
            attrs["raw_seconds"] = None
        return attrs


class EveusCounterBaseSensor(BaseEveusSensor):
    """Base implementation for counter cost sensors."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "₴"

    @property
    def native_value(self) -> float | None:
        """Return the counter value."""
        try:
            return float(self.coordinator.data[self._attr_entity_description.key])
        except (KeyError, TypeError, ValueError):
            return None


class EveusCounterACostSensor(EveusCounterBaseSensor):
    """Implementation of counter A cost sensor."""

    _attr_entity_description = SensorEntityDescription(
        key=ATTR_COUNTER_A_COST,
        name="Counter A Cost",
        icon="mdi:currency-uah",
    )


class EveusCounterBCostSensor(EveusCounterBaseSensor):
    """Implementation of counter B cost sensor."""

    _attr_entity_description = SensorEntityDescription(
        key=ATTR_COUNTER_B_COST,
        name="Counter B Cost",
        icon="mdi:currency-uah",
    )


class EveusStateOfChargeKwhSensor(BaseEveusSensor):
    """Implementation of State of Charge kWh sensor."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_description = SensorEntityDescription(
        key="soc_kwh",
        name="State of Charge kWh",
        icon="mdi:car-battery",
    )

    @property
    def native_value(self) -> float | None:
        """Calculate state of charge in kWh."""
        try:
            battery_capacity = self.coordinator.data.get("battery_capacity", 75)
            initial_soc = float(self.hass.states.get("input_number.eveus_initial_soc").state)
            if not 0 <= initial_soc <= 100:
                return None

            charged_kwh = float(self.coordinator.data[ATTR_COUNTER_A_ENERGY])
            correction = float(self.hass.states.get("input_number.eveus_soc_correction").state)

            initial_kwh = (initial_soc / 100) * battery_capacity
            efficiency = (1 - correction / 100)
            total_kwh = initial_kwh + (charged_kwh * efficiency)

            return round(max(0, min(total_kwh, battery_capacity)), 2)
        except (AttributeError, TypeError, ValueError):
            return None


class EveusStateOfChargePercentSensor(BaseEveusSensor):
    """Implementation of State of Charge percentage sensor."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_description = SensorEntityDescription(
        key="soc_percent",
        name="State of Charge %",
        icon="mdi:battery-charging",
    )

    @property
    def native_value(self) -> float | None:
        """Calculate state of charge percentage."""
        try:
            current_kwh = float(self.hass.states.get("sensor.eveus_soc_kwh").state)
            battery_capacity = self.coordinator.data.get("battery_capacity", 75)
            
            percentage = (current_kwh / battery_capacity * 100)
            return round(max(0, min(percentage, 100)), 0)
        except (AttributeError, TypeError, ValueError):
            return None


class EveusTimeToTargetSensor(BaseEveusSensor):
    """Implementation of Time to Target sensor."""

    _attr_entity_description = SensorEntityDescription(
        key="time_to_target",
        name="Time to Target",
        icon="mdi:timer",
    )

    @property
    def native_value(self) -> str:
        """Calculate and return time to target SOC."""
        try:
            if self.coordinator.data[ATTR_STATE] != 4:  # Not charging
                return "Not charging"

            current_soc = float(self.hass.states.get("sensor.eveus_soc_percent").state)
            if not 0 <= current_soc <= 100:
                return "unknown"

            target_soc = float(self.hass.states.get("input_number.eveus_target_soc").state)
            if target_soc <= current_soc:
                return "Target reached"

            power_meas = float(self.coordinator.data[ATTR_POWER])
            if power_meas < 100:
                return "Insufficient power"

            battery_capacity = self.coordinator.data.get("battery_capacity", 75)
            correction = float(self.hass.states.get("input_number.eveus_soc_correction").state)
            
            remaining_kwh = (target_soc - current_soc) * battery_capacity / 100
            power_kw = power_meas * (1 - correction / 100) / 1000
            total_minutes = round(remaining_kwh / power_kw * 60)

            if total_minutes < 1:
                return "Less than 1m"
            
            hours = total_minutes // 60
            minutes = total_minutes % 60
            
            if hours > 0:
                return f"{hours}h{' ' + str(minutes) + 'm' if minutes > 0 else ''}"
            return f"{minutes}m"
            
        except Exception:
            return "unknown"
