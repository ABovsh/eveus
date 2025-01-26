"""Support for Eveus sensors."""
from __future__ import annotations

import logging
import asyncio
import time
from datetime import datetime
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util
from homeassistant.helpers.template import Template
from homeassistant.components.text import TextEntity
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
    ATTR_BATTERY_VOLTAGE,
)

_LOGGER = logging.getLogger(__name__)

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
        self._session = None
        self._sensors = []
        self._update_task = None
        self._last_update = time.time()
        self._update_lock = asyncio.Lock()
        self._error_count = 0
        self._max_errors = 3

    def register_sensor(self, sensor: "BaseEveusSensor") -> None:
        """Register a sensor for updates."""
        self._sensors.append(sensor)

    @property
    def data(self) -> dict:
        """Return the latest data."""
        return self._data

    @property
    def available(self) -> bool:
        """Return if updater is available."""
        return self._available

    @property
    def last_update(self) -> float:
        """Return last update time."""
        return self._last_update

    async def async_start_updates(self) -> None:
        """Start the update loop."""
        if self._update_task is None:
            self._update_task = asyncio.create_task(self._update_loop())

    async def _update_loop(self) -> None:
        """Handle updates with improved error handling."""
        while True:
            try:
                await self._update()
                await asyncio.sleep(SCAN_INTERVAL.total_seconds())
            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.error("Error in update loop: %s", str(err))
                await asyncio.sleep(SCAN_INTERVAL.total_seconds())

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create client session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10, connect=5)
            connector = aiohttp.TCPConnector(limit=1, force_close=True)
            self._session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return self._session

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
                self._last_update = time.time()
                self._error_count = 0

                for sensor in self._sensors:
                    try:
                        sensor.async_write_ha_state()
                    except Exception as sensor_err:
                        _LOGGER.error(
                            "Error updating sensor %s: %s",
                            getattr(sensor, 'name', 'unknown'),
                            str(sensor_err)
                        )

        except Exception as err:
            self._error_count += 1
            self._available = False if self._error_count >= self._max_errors else True
            _LOGGER.error("Error updating data: %s", str(err))

    async def async_shutdown(self) -> None:
        """Shutdown the updater."""
        if self._update_task:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass
        if self._session and not self._session.closed:
            await self._session.close()

class BaseEveusSensor(SensorEntity, RestoreEntity):
    """Base implementation for all Eveus sensors."""

    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the sensor."""
        self._updater = updater
        self._updater.register_sensor(self)
        self._attr_has_entity_name = True
        self._previous_value = None
        self._attr_should_poll = False
        self._attr_entity_registry_enabled_default = True
        self._attr_entity_registry_visible_default = True

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state and state.state not in ('unknown', 'unavailable'):
            try:
                if hasattr(self, '_attr_suggested_display_precision'):
                    self._previous_value = float(state.state)
                else:
                    self._previous_value = state.state
            except (TypeError, ValueError):
                self._previous_value = state.state
        await self._updater.async_start_updates()

    async def async_will_remove_from_hass(self) -> None:
        """Handle entity removal."""
        await self._updater.async_shutdown()

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self._updater._host)},
            "name": "Eveus EV Charger",
            "manufacturer": "Eveus",
            "model": f"Eveus ({self._updater._host})",
            "sw_version": self._updater.data.get("verFWMain", "Unknown"),
            "hw_version": self._updater.data.get("verHW", "Unknown"),
        }

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        attrs = {
            "last_update": self._updater.last_update,
            "host": self._updater._host,
        }
        if self._previous_value is not None:
            attrs["previous_value"] = self._previous_value
        return attrs

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self._updater.available

class EveusNumericSensor(BaseEveusSensor):
    """Base class for numeric sensors."""
    
    _attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float | None:
        """Return the sensor value."""
        try:
            value = float(self._updater.data.get(self._key, 0))
            self._previous_value = value
            return round(value, self._attr_suggested_display_precision)
        except (TypeError, ValueError):
            return self._previous_value

class EveusEnergyBaseSensor(EveusNumericSensor):
    """Base energy sensor with improved precision."""
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 1

class EveusVoltageSensor(EveusNumericSensor):
    """Voltage sensor."""
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:lightning-bolt"
    _attr_suggested_display_precision = 0
    _key = ATTR_VOLTAGE

    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the sensor."""
        super().__init__(updater)
        self._attr_name = "Voltage"
        self._attr_unique_id = f"{updater._host}_voltage"

class EveusCurrentSensor(EveusNumericSensor):
    """Current sensor."""
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:current-ac"
    _attr_suggested_display_precision = 1
    _key = ATTR_CURRENT

    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the sensor."""
        super().__init__(updater)
        self._attr_name = "Current"
        self._attr_unique_id = f"{updater._host}_current"

class EveusPowerSensor(EveusNumericSensor):
    """Power sensor."""
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:flash"
    _attr_suggested_display_precision = 0
    _key = ATTR_POWER

    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the sensor."""
        super().__init__(updater)
        self._attr_name = "Power"
        self._attr_unique_id = f"{updater._host}_power"

class EveusCurrentSetSensor(EveusNumericSensor):
    """Current set sensor."""
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:current-ac"
    _key = ATTR_CURRENT_SET

    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the sensor."""
        super().__init__(updater)
        self._attr_name = "Current Set"
        self._attr_unique_id = f"{updater._host}_current_set"

class EveusSessionEnergySensor(EveusEnergyBaseSensor):
    """Session energy sensor."""
    _key = ATTR_SESSION_ENERGY
    _attr_icon = "mdi:battery-charging"

    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the sensor."""
        super().__init__(updater)
        self._attr_name = "Session Energy"
        self._attr_unique_id = f"{updater._host}_session_energy"

class EveusTotalEnergySensor(EveusEnergyBaseSensor):
    """Total energy sensor."""
    _key = ATTR_TOTAL_ENERGY
    _attr_icon = "mdi:battery-charging-100"

    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the sensor."""
        super().__init__(updater)
        self._attr_name = "Total Energy"
        self._attr_unique_id = f"{updater._host}_total_energy"

class EveusStateSensor(BaseEveusSensor):
    """Charging state sensor."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:information"

    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the sensor."""
        super().__init__(updater)
        self._attr_name = "State"
        self._attr_unique_id = f"{updater._host}_state"

    @property
    def native_value(self) -> str:
        """Return charging state."""
        try:
            return CHARGING_STATES.get(self._updater.data.get(ATTR_STATE, -1), "Unknown")
        except (TypeError, ValueError):
            return "Unknown"

class EveusSubstateSensor(BaseEveusSensor):
    """Substate sensor."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:information"

    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the sensor."""
        super().__init__(updater)
        self._attr_name = "Substate"
        self._attr_unique_id = f"{updater._host}_substate"

    @property
    def native_value(self) -> str:
        """Return substate with context."""
        try:
            state = self._updater.data.get(ATTR_STATE)
            substate = self._updater.data.get(ATTR_SUBSTATE)
            
            if state == 7:  # Error state
                return ERROR_STATES.get(substate, "Unknown Error")
            return NORMAL_SUBSTATES.get(substate, "Unknown State")
        except (TypeError, ValueError):
            return "Unknown"

class EveusEnabledSensor(BaseEveusSensor):
    """Enabled state sensor."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:power"

    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the sensor."""
        super().__init__(updater)
        self._attr_name = "Enabled"
        self._attr_unique_id = f"{updater._host}_enabled"

    @property
    def native_value(self) -> str:
        """Return if charging is enabled."""
        try:
            return "Yes" if self._updater.data.get(ATTR_ENABLED) == 1 else "No"
        except (TypeError, ValueError):
            return "Unknown"

class EveusGroundSensor(BaseEveusSensor):
    """Ground sensor."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:electric-switch"

    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the sensor."""
        super().__init__(updater)
        self._attr_name = "Ground"
        self._attr_unique_id = f"{updater._host}_ground"

    @property
    def native_value(self) -> str:
        """Return ground status."""
        try:
            return "Connected" if self._updater.data.get(ATTR_GROUND) == 1 else "Not Connected"
        except (TypeError, ValueError):
            return "Unknown"

class EveusBoxTemperatureSensor(EveusNumericSensor):
    """Box temperature sensor."""
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:thermometer"
    
    _key = ATTR_TEMPERATURE_BOX

    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the sensor."""
        super().__init__(updater)
        self._attr_name = "Box Temperature"
        self._attr_unique_id = f"{updater._host}_box_temperature"

class EveusPlugTemperatureSensor(EveusNumericSensor):
    """Plug temperature sensor."""
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:thermometer-high"
    _key = ATTR_TEMPERATURE_PLUG

    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the sensor."""
        super().__init__(updater)
        self._attr_name = "Plug Temperature"
        self._attr_unique_id = f"{updater._host}_plug_temperature"

class EveusSystemTimeSensor(BaseEveusSensor):
    """System time sensor."""
    _attr_icon = "mdi:clock-outline"

    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the sensor."""
        super().__init__(updater)
        self._attr_name = "System Time"
        self._attr_unique_id = f"{updater._host}_system_time"

    @property
    def native_value(self) -> str:
        """Return formatted system time."""
        try:
            timestamp = int(self._updater.data.get(ATTR_SYSTEM_TIME, 0))
            return datetime.fromtimestamp(timestamp).strftime("%H:%M")
        except (TypeError, ValueError):
            return "unknown"

class EveusSessionTimeSensor(BaseEveusSensor):
    """Session time sensor."""
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:timer"

    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the sensor."""
        super().__init__(updater)
        self._attr_name = "Session Time"
        self._attr_unique_id = f"{updater._host}_session_time"
    
    @property
    def native_value(self) -> int:
        """Return the session time in seconds."""
        try:
            return int(self._updater.data.get(ATTR_SESSION_TIME, 0))
        except (TypeError, ValueError):
            return 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        attrs = super().extra_state_attributes
        try:
            seconds = int(self._updater.data.get(ATTR_SESSION_TIME, 0))
            days = seconds // 86400
            hours = (seconds % 86400) // 3600
            minutes = (seconds % 3600) // 60

            if days > 0:
                formatted_time = f"{days}d {hours:02d}h {minutes:02d}m"
            elif hours > 0:
                formatted_time = f"{hours}h {minutes:02d}m"
            else:
                formatted_time = f"{minutes}m"
            
            attrs["formatted_time"] = formatted_time
            
        except (TypeError, ValueError):
            attrs["formatted_time"] = "0m"
            
        return attrs

class EveusCounterAEnergySensor(EveusEnergyBaseSensor):
    """Counter A energy sensor."""
    _key = ATTR_COUNTER_A_ENERGY
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:counter"

    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the sensor."""
        super().__init__(updater)
        self._attr_name = "Counter A Energy"
        self._attr_unique_id = f"{updater._host}_counter_a_energy"

class EveusCounterBEnergySensor(EveusEnergyBaseSensor):
    """Counter B energy sensor."""
    _key = ATTR_COUNTER_B_ENERGY
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:counter"

    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the sensor."""
        super().__init__(updater)
        self._attr_name = "Counter B Energy"
        self._attr_unique_id = f"{updater._host}_counter_b_energy"

class EveusCounterACostSensor(EveusNumericSensor):
    """Counter A cost sensor."""
    _attr_native_unit_of_measurement = "₴"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:currency-uah"
    _key = ATTR_COUNTER_A_COST

    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the sensor."""
        super().__init__(updater)
        self._attr_name = "Counter A Cost"
        self._attr_unique_id = f"{updater._host}_counter_a_cost"

class EveusCounterBCostSensor(EveusNumericSensor):
    """Counter B cost sensor."""
    _attr_native_unit_of_measurement = "₴"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:currency-uah"
    _key = ATTR_COUNTER_B_COST

    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the sensor."""
        super().__init__(updater)
        self._attr_name = "Counter B Cost"
        self._attr_unique_id = f"{updater._host}_counter_b_cost"

class EveusBatteryVoltageSensor(EveusNumericSensor):
    """Battery voltage sensor."""
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery"
    _key = ATTR_BATTERY_VOLTAGE

    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the sensor."""
        super().__init__(updater)
        self._attr_name = "Battery Voltage"
        self._attr_unique_id = f"{updater._host}_battery_voltage"

class EVSocKwhSensor(BaseEveusSensor):
    """EV State of Charge energy sensor."""
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:battery-charging"
    _attr_suggested_display_precision = 0
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the sensor."""
        super().__init__(updater)
        self._attr_name = "SOC Energy"
        self._attr_unique_id = f"{updater._host}_soc_kwh"

    @property
    def native_value(self) -> float | None:
        """Calculate and return state of charge in kWh."""
        try:
            initial_soc = float(self.hass.states.get("input_number.ev_initial_soc").state)
            max_capacity = float(self.hass.states.get("input_number.ev_battery_capacity").state)
            energy_charged = float(self._updater.data.get("IEM1", 0))
            correction = float(self.hass.states.get("input_number.ev_soc_correction").state)

            if initial_soc < 0 or initial_soc > 100 or max_capacity <= 0:
                return None

            initial_kwh = (initial_soc / 100) * max_capacity
            efficiency = (1 - correction / 100)
            charged_kwh = energy_charged * efficiency
            total_kwh = initial_kwh + charged_kwh
            
            return round(max(0, min(total_kwh, max_capacity)), 2)
        except (TypeError, ValueError, AttributeError):
            return None

class EVSocPercentSensor(BaseEveusSensor):
    """EV State of Charge percentage sensor."""
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:battery-charging"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    

    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the sensor."""
        super().__init__(updater)
        self._attr_name = "SOC Percent"
        self._attr_unique_id = f"{updater._host}_soc_percent"

    @property
    def native_value(self) -> float | None:
        """Return the state of charge percentage."""
        try:
            soc_kwh = float(self.hass.states.get("sensor.eveus_ev_charger_soc_energy").state)
            max_capacity = float(self.hass.states.get("input_number.ev_battery_capacity").state)
            
            if soc_kwh >= 0 and max_capacity > 0:
                percentage = round((soc_kwh / max_capacity * 100), 0)
                return max(0, min(percentage, 100))
            return None
        except (TypeError, ValueError, AttributeError):
            return None

class TimeToTargetSocSensor(TextEntity):
   """Time to target SOC text entity."""
   _attr_icon = "mdi:timer"
   _attr_pattern = None
   _attr_mode = "text"

   def __init__(self, updater: EveusUpdater) -> None:
       """Initialize the text entity."""
       self._updater = updater
       self._attr_name = "Time to Target"
       self._attr_unique_id = f"{updater._host}_time_to_target"

   @property
   def native_value(self) -> str:
       """Calculate and return formatted time to target."""
       try:
           current_soc = float(self.hass.states.get("sensor.eveus_ev_charger_soc_percent").state)
           target_soc = float(self.hass.states.get("input_number.ev_target_soc").state)
           power_meas = float(self._updater.data.get(ATTR_POWER, 0))
           battery_capacity = float(self.hass.states.get("input_number.ev_battery_capacity").state)
           correction = float(self.hass.states.get("input_number.ev_soc_correction").state)

           remaining_kwh = (target_soc - current_soc) * battery_capacity / 100
           efficiency = (1 - correction / 100)
           power_kw = power_meas * efficiency / 1000
           
           if power_kw <= 0:
               return "-"

           total_minutes = round((remaining_kwh / power_kw * 60), 0)
           
           if total_minutes < 1:
               return "< 1m"

           days = int(total_minutes // 1440)
           hours = int((total_minutes % 1440) // 60)
           minutes = int(total_minutes % 60)

           parts = []
           if days > 0:
               parts.append(f"{days}d")
           if hours > 0:
               parts.append(f"{hours}h")
           if minutes > 0 or not parts:
               parts.append(f"{minutes}m")

           return " ".join(parts)

       except (TypeError, ValueError, AttributeError):
           return "-"

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Eveus sensors."""
    updater = EveusUpdater(
        host=entry.data[CONF_HOST],
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        hass=hass,
    )

    # Create all sensor entities
    sensors = [
        # Basic measurements
        EveusVoltageSensor(updater),
        EveusCurrentSensor(updater),
        EveusPowerSensor(updater),
        EveusCurrentSetSensor(updater),
        EveusSessionEnergySensor(updater),
        EveusTotalEnergySensor(updater),
        
        # Diagnostic sensors
        EveusStateSensor(updater),
        EveusSubstateSensor(updater),
        EveusEnabledSensor(updater),
        EveusGroundSensor(updater),
        
        # Temperature sensors
        EveusBoxTemperatureSensor(updater),
        EveusPlugTemperatureSensor(updater),
        EveusBatteryVoltageSensor(updater),
        
        # Time and session sensors
        EveusSystemTimeSensor(updater),
        EveusSessionTimeSensor(updater),
        
        # Energy and cost counters
        EveusCounterAEnergySensor(updater),
        EveusCounterBEnergySensor(updater),
        EveusCounterACostSensor(updater),
        EveusCounterBCostSensor(updater),
        
        # EV-specific sensors
        EVSocKwhSensor(updater),
        EVSocPercentSensor(updater),
        TimeToTargetSocSensor(updater),
    ]

    # Initialize entities dict if needed
    if "entities" not in hass.data[DOMAIN][entry.entry_id]:
        hass.data[DOMAIN][entry.entry_id]["entities"] = {}

    # Store sensor references with unique_id as key
    hass.data[DOMAIN][entry.entry_id]["entities"]["sensor"] = {
        sensor.unique_id: sensor for sensor in sensors
    }

    async_add_entities(sensors)
