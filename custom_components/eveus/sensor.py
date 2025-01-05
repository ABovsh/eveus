"""Support for Eveus sensors."""
from __future__ import annotations

import logging
import asyncio
import aiohttp
from typing import Any

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
        self._attr_unique_id = f"{updater._host}_{self.name}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, updater._host)},
            "name": "Eveus EV Charger",
            "manufacturer": "Eveus",
        }
        _LOGGER.debug("Initialized sensor: %s", self.name)

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()
        await self._updater.start_updates()

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self._updater.available

class EveusVoltageSensor(BaseEveusSensor):
    """Voltage sensor."""
    name = "voltage"
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> StateType:
        """Return the voltage."""
        try:
            return float(self._updater.data[ATTR_VOLTAGE])
        except (KeyError, TypeError, ValueError):
            return None

class EveusCurrentSensor(BaseEveusSensor):
    """Current sensor."""
    name = "current"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_state_class = SensorStateClass.MEASUREMENT

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

    @property
    def native_value(self) -> StateType:
        """Return power consumption."""
        try:
            return float(self._updater.data[ATTR_POWER])
        except (KeyError, TypeError, ValueError):
            return None

class EveusSessionEnergySensor(BaseEveusSensor):
    """Session energy sensor."""
    name = "session_energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self) -> StateType:
        """Return session energy."""
        try:
            return float(self._updater.data[ATTR_SESSION_ENERGY])
        except (KeyError, TypeError, ValueError):
            return None

class EveusTotalEnergySensor(BaseEveusSensor):
    """Total energy sensor."""
    name = "total_energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self) -> StateType:
        """Return total energy."""
        try:
            return float(self._updater.data[ATTR_TOTAL_ENERGY])
        except (KeyError, TypeError, ValueError):
            return None

class EveusSessionTimeSensor(BaseEveusSensor):
    """Session time sensor."""
    name = "session_time"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> StateType:
        """Return session time in seconds."""
        try:
            return int(self._updater.data[ATTR_SESSION_TIME])
        except (KeyError, TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Return formatted time as attribute."""
        try:
            seconds = int(self._updater.data[ATTR_SESSION_TIME])
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
                
            return {"formatted_time": " ".join(parts) if parts else "0m"}
        except (KeyError, TypeError, ValueError):
            return {"formatted_time": "unknown"}

class EveusStateSensor(BaseEveusSensor):
    """Charging state sensor."""
    name = "state"

    @property
    def native_value(self) -> str:
        """Return charging state."""
        try:
            return CHARGING_STATES.get(self._updater.data[ATTR_STATE], "Unknown")
        except (KeyError, TypeError):
            return "Unknown"

class EveusSubstateSensor(BaseEveusSensor):
    """Substate sensor."""
    name = "substate"

    @property
    def native_value(self) -> str:
        """Return substate with context."""
        try:
            state = self._updater.data[ATTR_STATE]
            substate = self._updater.data[ATTR_SUBSTATE]
            
            if state == 7:  # Error state
                return ERROR_STATES.get(substate, "Unknown Error")
            return NORMAL_SUBSTATES.get(substate, "Unknown State")
        except (KeyError, TypeError):
            return "Unknown"

class EveusCurrentSetSensor(BaseEveusSensor):
    """Current set sensor."""
    name = "current_set"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> StateType:
        """Return current set point."""
        try:
            return float(self._updater.data[ATTR_CURRENT_SET])
        except (KeyError, TypeError, ValueError):
            return None

class EveusEnabledSensor(BaseEveusSensor):
    """Enabled state sensor."""
    name = "enabled"

    @property
    def native_value(self) -> str:
        """Return if charging is enabled."""
        try:
            return "Yes" if self._updater.data[ATTR_ENABLED] == 1 else "No"
        except (KeyError, TypeError):
            return "Unknown"

class EveusBoxTemperatureSensor(BaseEveusSensor):
    """Box temperature sensor."""
    name = "box_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> StateType:
        """Return box temperature."""
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

    @property
    def native_value(self) -> StateType:
        """Return plug temperature."""
        try:
            return float(self._updater.data[ATTR_TEMPERATURE_PLUG])
        except (KeyError, TypeError, ValueError):
            return None

class EveusSystemTimeSensor(BaseEveusSensor):
    """System time sensor."""
    name = "system_time"

    @property
    def native_value(self) -> StateType:
        """Return system time."""
        try:
            return int(self._updater.data[ATTR_SYSTEM_TIME])
        except (KeyError, TypeError, ValueError):
            return None

class EveusCounterAEnergySensor(BaseEveusSensor):
    """Counter A energy sensor."""
    name = "counter_a_energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self) -> StateType:
        """Return Counter A energy."""
        try:
            return float(self._updater.data[ATTR_COUNTER_A_ENERGY])
        except (KeyError, TypeError, ValueError):
            return None

class EveusCounterBEnergySensor(BaseEveusSensor):
    """Counter B energy sensor."""
    name = "counter_b_energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self) -> StateType:
        """Return Counter B energy."""
        try:
            return float(self._updater.data[ATTR_COUNTER_B_ENERGY])
        except (KeyError, TypeError, ValueError):
            return None

class EveusCounterACostSensor(BaseEveusSensor):
    """Counter A cost sensor."""
    name = "counter_a_cost"
    _attr_native_unit_of_measurement = "₴"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self) -> StateType:
        """Return Counter A cost."""
        try:
            return float(self._updater.data[ATTR_COUNTER_A_COST])
        except (KeyError, TypeError, ValueError):
            return None

class EveusCounterBCostSensor(BaseEveusSensor):
    """Counter B cost sensor."""
    name = "counter_b_cost"
    _attr_native_unit_of_measurement = "₴"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self) -> StateType:
        """Return Counter B cost."""
        try:
            return float(self._updater.data[ATTR_COUNTER_B_COST])
        except (KeyError, TypeError, ValueError):
            return None
