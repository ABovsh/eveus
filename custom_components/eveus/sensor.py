from __future__ import annotations

import logging
import asyncio
import aiohttp
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfElectricPotential,
    UnitOfElectricCurrent,
    UnitOfPower,
    UnitOfEnergy,
    UnitOfTemperature,
    UnitOfTime,
    CONF_HOST,
    CONF_USERNAME,
    CONF_PASSWORD,
)
from homeassistant.helpers.typing import StateType

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

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Eveus sensors."""
    _LOGGER.debug("Setting up Eveus sensors")
    
    updater = EveusUpdater(
        host=entry.data[CONF_HOST],
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        hass=hass,
    )

    entities = [
        EveusVoltageSensor(updater),
        EveusCurrentSensor(updater),
        EveusPowerSensor(updater),
        EveusCurrentSetSensor(updater),
        EveusSessionEnergySensor(updater),
        EveusTotalEnergySensor(updater),
        EveusStateSensor(updater),
        EveusSubstateSensor(updater),
        EveusEnabledSensor(updater),
        EveusGroundSensor(updater),
        EveusBoxTemperatureSensor(updater),
        EveusPlugTemperatureSensor(updater),
        EveusSystemTimeSensor(updater),
        EveusSessionTimeSensor(updater),
        EveusCounterAEnergySensor(updater),
        EveusCounterBEnergySensor(updater),
        EveusCounterACostSensor(updater),
        EveusCounterBCostSensor(updater),
    ]
    
    async_add_entities(entities)
    _LOGGER.debug("Added %s Eveus entities", len(entities))

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
        self._session = None
        _LOGGER.debug("Initialized updater for host: %s", host)

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create client session."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def start_updates(self) -> None:
        """Start the update loop."""
        if self._update_task:
            return

        async def update_loop() -> None:
            """Handle updates."""
            try:
                while True:
                    try:
                        await self._update()
                        for sensor in self._sensors:
                            sensor.async_write_ha_state()
                    except Exception as err:
                        self._available = False
                        _LOGGER.error("Error updating Eveus data: %s", err)
                    await asyncio.sleep(SCAN_INTERVAL.total_seconds())
            finally:
                if self._session:
                    await self._session.close()
                    self._session = None

        self._update_task = self._hass.loop.create_task(update_loop())
        _LOGGER.debug("Started update loop")

    def register_sensor(self, sensor: "BaseEveusSensor") -> None:
        """Register a sensor for updates."""
        self._sensors.append(sensor)

    async def _update(self) -> None:
        """Update the data."""
        try:
            session = await self._get_session()
            async with session.post(
                f"http://{self._host}/main",
                auth=aiohttp.BasicAuth(self._username, self._password),
                timeout=10
            ) as response:
                response.raise_for_status()
                self._data = await response.json()
                self._available = True
                _LOGGER.debug("Data updated successfully")
        except Exception as err:
            self._available = False
            raise

    @property
    def data(self) -> dict[str, Any]:
        """Return the latest data."""
        return self._data

    @property
    def available(self) -> bool:
        """Return if updater is available."""
        return self._available

class BaseEveusSensor(SensorEntity):
    """Base implementation for all Eveus sensors."""

    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the sensor."""
        self._updater = updater
        self._updater.register_sensor(self)
        self._attr_has_entity_name = True
        self._attr_should_poll = False
        self._attr_unique_id = f"eveus_{self.name}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, "eveus")},
            "name": "Eveus EV Charger",
            "manufacturer": "Eveus",
            "model": f"Eveus (eveus)",
            "sw_version": updater.data.get("verFWMain", "Unknown"),
        }
        _LOGGER.debug("Initialized sensor: %s", self.name)

    @property
    def name(self) -> str:
        """Return the display name of the sensor."""
        return f"Eveus {self.name.replace('_', ' ').title()}"

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()
        await self._updater.start_updates()

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self._updater.available

# Define other sensor classes similarly with the correct device class

class EveusEnabledSensor(BaseEveusSensor):
    """Enabled sensor."""
    name = "enabled"
    _attr_device_class = SensorDeviceClass.ENUM  # Use ENUM for boolean-like states
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unique_id = "eveus_enabled"

    @property
    def native_value(self) -> StateType:
        """Return the enabled state."""
        try:
            return bool(self._updater.data[ATTR_ENABLED])
        except (KeyError, TypeError, ValueError):
            return None

class EveusVoltageSensor(BaseEveusSensor):
    """Voltage sensor."""
    name = "voltage"
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unique_id = "eveus_voltage"

    @property
    def native_value(self) -> StateType:
        """Return the voltage."""
        try:
            return float(self._updater.data[ATTR_VOLTAGE])
        except (KeyError, TypeError, ValueError):
            return None

# Repeat for other sensors, ensuring device classes are correctly assigned

class EveusCurrentSensor(BaseEveusSensor):
    """Current sensor."""
    name = "current"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unique_id = "eveus_current"

    @property
    def native_value(self) -> StateType:
        """Return the current."""
        try:
            return float(self._updater.data[ATTR_CURRENT])
        except (KeyError, TypeError, ValueError):
            return None

class EveusPowerSensor(BaseEveusSensor):
    """Power sensor."""
    name = "power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unique_id = "eveus_power"

    @property
    def native_value(self) -> StateType:
        """Return the power."""
        try:
            return float(self._updater.data[ATTR_POWER])
        except (KeyError, TypeError, ValueError):
            return None

class EveusCurrentSetSensor(BaseEveusSensor):
    """Current set sensor."""
    name = "current_set"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unique_id = "eveus_current_set"

    @property
    def native_value(self) -> StateType:
        """Return the current set."""
        try:
            return float(self._updater.data[ATTR_CURRENT_SET])
        except (KeyError, TypeError, ValueError):
            return None

class EveusSessionEnergySensor(BaseEveusSensor):
    """Session energy sensor."""
    name = "session_energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unique_id = "eveus_session_energy"

    @property
    def native_value(self) -> StateType:
        """Return the session energy."""
        try:
            return float(self._updater.data[ATTR_SESSION_ENERGY])
        except (KeyError, TypeError, ValueError):
            return None

class EveusTotalEnergySensor(BaseEveusSensor):
    """Total energy sensor."""
    name = "total_energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unique_id = "eveus_total_energy"

    @property
    def native_value(self) -> StateType:
        """Return the total energy."""
        try:
            return float(self._updater.data[ATTR_TOTAL_ENERGY])
        except (KeyError, TypeError, ValueError):
            return None

class EveusStateSensor(BaseEveusSensor):
    """State sensor."""
    name = "state"
    _attr_device_class = SensorDeviceClass.ENUM  # ENUM for states
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unique_id = "eveus_state"

    @property
    def native_value(self) -> StateType:
        """Return the state."""
        try:
            return str(self._updater.data[ATTR_STATE])
        except (KeyError, TypeError, ValueError):
            return None

class EveusSubstateSensor(BaseEveusSensor):
    """Substate sensor."""
    name = "substate"
    _attr_device_class = SensorDeviceClass.ENUM  # ENUM for states
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unique_id = "eveus_substate"

    @property
    def native_value(self) -> StateType:
        """Return the substate."""
        try:
            return str(self._updater.data[ATTR_SUBSTATE])
        except (KeyError, TypeError, ValueError):
            return None

class EveusGroundSensor(BaseEveusSensor):
    """Ground sensor."""
    name = "ground"
    _attr_device_class = SensorDeviceClass.SAFETY  # Use appropriate class for safety
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unique_id = "eveus_ground"

    @property
    def native_value(self) -> StateType:
        """Return the ground status."""
        try:
            return bool(self._updater.data[ATTR_GROUND])
        except (KeyError, TypeError, ValueError):
            return None

class EveusBoxTemperatureSensor(BaseEveusSensor):
    """Box temperature sensor."""
    name = "box_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unique_id = "eveus_box_temperature"

    @property
    def native_value(self) -> StateType:
        """Return the box temperature."""
        try:
            return float(self._updater.data[ATTR_TEMPERATURE_BOX])
        except (KeyError, TypeError, ValueError):
            return None

class EveusPlugTemperatureSensor(BaseEveusSensor):
    """Plug temperature sensor."""
    name = "plug_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unique_id = "eveus_plug_temperature"

    @property
    def native_value(self) -> StateType:
        """Return the plug temperature."""
        try:
            return float(self._updater.data[ATTR_TEMPERATURE_PLUG])
        except (KeyError, TypeError, ValueError):
            return None

class EveusSystemTimeSensor(BaseEveusSensor):
    """System time sensor."""
    name = "system_time"
    _attr_device_class = SensorDeviceClass.TIMESTAMP  # Use timestamp for time
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unique_id = "eveus_system_time"

    @property
    def native_value(self) -> StateType:
        """Return the system time."""
        try:
            return str(self._updater.data[ATTR_SYSTEM_TIME])
        except (KeyError, TypeError, ValueError):
            return None

class EveusSessionTimeSensor(BaseEveusSensor):
    """Session time sensor."""
    name = "session_time"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unique_id = "eveus_session_time"

    @property
    def native_value(self) -> StateType:
        """Return the session time."""
        try:
            return int(self._updater.data[ATTR_SESSION_TIME])
        except (KeyError, TypeError, ValueError):
            return None

class EveusCounterAEnergySensor(BaseEveusSensor):
    """Counter A energy sensor."""
    name = "counter_a_energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unique_id = "eveus_counter_a_energy"

    @property
    def native_value(self) -> StateType:
        """Return the counter A energy."""
        try:
            return float(self._updater.data[ATTR_COUNTER_A_ENERGY])
        except (KeyError, TypeError, ValueError):
            return None

class EveusCounterBEnergySensor(BaseEveusSensor):
    """Counter B energy sensor."""
    name = "counter_b_energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unique_id = "eveus_counter_b_energy"

    @property
    def native_value(self) -> StateType:
        """Return the counter B energy."""
        try:
            return float(self._updater.data[ATTR_COUNTER_B_ENERGY])
        except (KeyError, TypeError, ValueError):
            return None

class EveusCounterACostSensor(BaseEveusSensor):
    """Counter A cost sensor."""
    name = "counter_a_cost"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "USD"  # Change this based on your currency
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unique_id = "eveus_counter_a_cost"

    @property
    def native_value(self) -> StateType:
        """Return the counter A cost."""
        try:
            return float(self._updater.data[ATTR_COUNTER_A_COST])
        except (KeyError, TypeError, ValueError):
            return None

class EveusCounterBCostSensor(BaseEveusSensor):
    """Counter B cost sensor."""
    name = "counter_b_cost"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "USD"  # Change this based on your currency
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unique_id = "eveus_counter_b_cost"

    @property
    def native_value(self) -> StateType:
        """Return the counter B cost."""
        try:
            return float(self._updater.data[ATTR_COUNTER_B_COST])
        except (KeyError, TypeError, ValueError):
            return None
