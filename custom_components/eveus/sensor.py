# File: custom_components/eveus/sensor.py

"""Support for Eveus sensors."""
from __future__ import annotations

import logging
import asyncio
import aiohttp
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util
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
        self._update_task = None
        self._sensors = []
        self._session = None
        self._last_update = None
        self._consecutive_errors = 0
        self._max_consecutive_errors = 3
        _LOGGER.debug("Initialized updater for host: %s", host)

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create client session."""
        if self._session is None or self._session.closed:
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
                        self._last_update = dt_util.utcnow()
                        for sensor in self._sensors:
                            sensor.async_write_ha_state()
                    except Exception as err:
                        self._available = False
                        _LOGGER.error("Error updating Eveus data: %s", err)
                    await asyncio.sleep(SCAN_INTERVAL.total_seconds())
            finally:
                if self._session and not self._session.closed:
                    await self._session.close()
                    self._session = None

        self._update_task = self._hass.loop.create_task(update_loop())
        _LOGGER.debug("Started update loop for %s", self._host)

    async def _update(self) -> None:
        """Update the data with improved error handling."""
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
                self._consecutive_errors = 0
                _LOGGER.debug(
                    "Updated data for %s: State=%s, Power=%sW", 
                    self._host,
                    self._data.get("state"), 
                    self._data.get("powerMeas")
                )
        except aiohttp.ClientResponseError as error:
            self._consecutive_errors += 1
            self._available = False
            _LOGGER.error(
                "HTTP error updating data for %s: %s [status=%s]",
                self._host,
                error.message,
                error.status,
            )
            if self._consecutive_errors >= self._max_consecutive_errors:
                _LOGGER.warning("Multiple consecutive HTTP errors, may need attention")
            raise
        except aiohttp.ClientError as error:
            self._consecutive_errors += 1
            self._available = False
            _LOGGER.error(
                "Connection error updating data for %s: %s",
                self._host,
                str(error),
            )
            if self._consecutive_errors >= self._max_consecutive_errors:
                _LOGGER.warning("Multiple consecutive connection errors, may need attention")
            raise
        except Exception as error:
            self._consecutive_errors += 1
            self._available = False
            _LOGGER.error(
                "Unexpected error updating data for %s: %s",
                self._host,
                str(error),
            )
            if self._consecutive_errors >= self._max_consecutive_errors:
                _LOGGER.warning("Multiple consecutive errors, may need attention")
            raise

    def register_sensor(self, sensor: "BaseEveusSensor") -> None:
        """Register a sensor for updates."""
        self._sensors.append(sensor)

    async def async_shutdown(self) -> None:
        """Shutdown the updater."""
        if self._update_task:
            self._update_task.cancel()
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    @property
    def data(self) -> dict[str, Any]:
        """Return the latest data."""
        return self._data

    @property
    def available(self) -> bool:
        """Return if updater is available."""
        return self._available

    @property
    def last_update(self) -> datetime | None:
        """Return the last update timestamp."""
        return self._last_update

class BaseEveusSensor(SensorEntity, RestoreEntity):
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
            "model": f"Eveus ({updater._host})",
            "sw_version": updater.data.get("verFWMain", "Unknown"),
        }
        self._previous_value = None

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
        await self._updater.start_updates()

    async def async_will_remove_from_hass(self) -> None:
        """Handle removal of entity."""
        await self._updater.async_shutdown()

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self._updater.available

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        attrs = {
            "last_update": self._updater.last_update,
            "host": self._updater._host,
            "firmware": self._updater.data.get("verFWMain", "Unknown"),
        }
        if self._previous_value is not None:
            attrs["previous_value"] = self._previous_value
        return attrs

class EveusNumericSensor(BaseEveusSensor):
    """Base class for numeric sensors."""
    
    _attr_suggested_display_precision = 2

    @property
    def native_value(self) -> StateType:
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
    _attr_suggested_display_precision = 2

class EveusVoltageSensor(EveusNumericSensor):
    """Voltage sensor."""
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:lightning-bolt"
    _attr_suggested_display_precision = 1
    _key = ATTR_VOLTAGE
    name = "Voltage"

class EveusCurrentSensor(EveusNumericSensor):
    """Current sensor."""
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:current-ac"
    _attr_suggested_display_precision = 1
    _key = ATTR_CURRENT
    name = "Current"

class EveusPowerSensor(EveusNumericSensor):
    """Power sensor."""
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:flash"
    _attr_suggested_display_precision = 0
    _key = ATTR_POWER
    name = "Power"

class EveusCurrentSetSensor(EveusNumericSensor):
    """Current set sensor."""
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:current-ac"
    _key = ATTR_CURRENT_SET
    name = "Current Set"

class EveusSessionEnergySensor(EveusEnergyBaseSensor):
    """Session energy sensor."""
    _key = ATTR_SESSION_ENERGY
    _attr_icon = "mdi:battery-charging"
    name = "Session Energy"

class EveusTotalEnergySensor(EveusEnergyBaseSensor):
    """Total energy sensor."""
    _key = ATTR_TOTAL_ENERGY
    _attr_icon = "mdi:battery-charging-100"
    name = "Total Energy"

class EveusStateSensor(BaseEveusSensor):
    """Charging state sensor."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:ev-station"
    name = "State"

    @property
    def native_value(self) -> str:
        """Return charging state."""
        try:
            return CHARGING_STATES.get(self._updater.data[ATTR_STATE], "Unknown")
        except (KeyError, TypeError):
            return "Unknown"

class EveusSubstateSensor(BaseEveusSensor):
    """Substate sensor."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:information"
    name = "Substate"

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

class EveusEnabledSensor(BaseEveusSensor):
    """Enabled state sensor."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:power"
    name = "Enabled"

    @property
    def native_value(self) -> str:
        """Return if charging is enabled."""
        try:
            return "Yes" if self._updater.data[ATTR_ENABLED] == 1 else "No"
        except (KeyError, TypeError):
            return "Unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        attrs = super().extra_state_attributes
        try:
            is_enabled = self._updater.data[ATTR_ENABLED] == 1
            attrs["status"] = "Active" if is_enabled else "Inactive"
            attrs["charging_allowed"] = is_enabled
        except (KeyError, TypeError):
            attrs["status"] = "Unknown"
            attrs["charging_allowed"] = None
        return attrs

class EveusGroundSensor(BaseEveusSensor):
    """Ground sensor."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:electric-switch"
    name = "Ground"
    
    @property
    def native_value(self) -> str:
        """Return ground status."""
        try:
            return "Connected" if self._updater.data[ATTR_GROUND] == 1 else "Not Connected"
        except (KeyError, TypeError):
            return "Unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        attrs = super().extra_state_attributes
        try:
            is_grounded = self._updater.data[ATTR_GROUND] == 1
            attrs["status"] = "Normal" if is_grounded else "Warning"
            attrs["safety_check"] = "Passed" if is_grounded else "Failed"
        except (KeyError, TypeError):
            attrs["status"] = "Unknown"
            attrs["safety_check"] = "Unknown"
        return attrs

class EveusBoxTemperatureSensor(EveusNumericSensor):
    """Box temperature sensor."""
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:thermometer"
    _key = ATTR_TEMPERATURE_BOX
    name = "Box Temperature"

class EveusPlugTemperatureSensor(EveusNumericSensor):
    """Plug temperature sensor."""
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:thermometer-high"
    _key = ATTR_TEMPERATURE_PLUG
    name = "Plug Temperature"

class EveusSystemTimeSensor(BaseEveusSensor):
    """System time sensor."""
    _key = ATTR_SYSTEM_TIME
    _attr_icon = "mdi:clock-outline"
    name = "System Time"

    @property
    def native_value(self) -> str:
        """Return formatted system time."""
        try:
            timestamp = int(self._updater.data.get(self._key, 0))
            dt = datetime.fromtimestamp(timestamp)
            return dt.strftime("%H:%M")
        except (KeyError, TypeError, ValueError):
            return "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        attrs = super().extra_state_attributes
        try:
            timestamp = int(self._updater.data.get(self._key, 0))
            dt = datetime.fromtimestamp(timestamp)
            attrs["full_date"] = dt.strftime("%Y-%m-%d %H:%M:%S")
            attrs["raw_timestamp"] = timestamp
        except (KeyError, TypeError, ValueError):
            pass
        return attrs

class EveusSessionTimeSensor(EveusNumericSensor):
    """Session time sensor."""
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:timer"
    _key = ATTR_SESSION_TIME
    name = "Session Time"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return formatted time as attribute."""
        attrs = super().extra_state_attributes
        try:
            seconds = int(self._updater.data[self._key])
            days = seconds // 86400
            hours = (seconds % 86400) // 3600
            minutes = (seconds % 3600) // 60
            
            if days > 0:
                attrs["formatted_time"] = f"{days}d {hours:02d}h {minutes:02d}m"
            elif hours > 0:
                attrs["formatted_time"] = f"{hours}h {minutes:02d}m"
            else:
                attrs["formatted_time"] = f"{minutes}m"
        except (KeyError, TypeError, ValueError):
            attrs["formatted_time"] = "unknown"
        return attrs

class EVSocKwhSensor(BaseEveusSensor):
    """EV State of Charge energy sensor."""
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:battery-charging"
    _attr_suggested_display_precision = 2
    name = "EV State of Charge"

    def _get_input_values(self) -> dict:
        """Get all required input values."""
        try:
            return {
                'initial_soc': float(self.hass.states.get("input_number.initial_ev_soc").state),
                'max_capacity': float(self.hass.states.get("input_number.ev_battery_capacity").state),
                'energy_charged': float(self._updater.data.get("IEM1", 0)),
                'correction': float(self.hass.states.get("input_number.ev_soc_correction").state)
            }
        except (AttributeError, TypeError, ValueError) as err:
            _LOGGER.debug("Error getting input values: %s", err)
            return {}

    def _is_valid_input(self, data: dict) -> bool:
        """Validate input values."""
        return (data.get('initial_soc', -1) >= 0 and
                data.get('initial_soc', 101) <= 100 and
                data.get('max_capacity', 0) > 0 and
                isinstance(data.get('energy_charged', None), (int, float)))

    @property
    def native_value(self) -> float | str:
        """Calculate and return state of charge in kWh."""
        data = self._get_input_values()
        
        if not self._is_valid_input(data):
            return "unknown"

        try:
            initial_kwh = (data['initial_soc'] / 100) * data['max_capacity']
            efficiency = (1 - data['correction'] / 100)
            charged_kwh = data['energy_charged'] * efficiency
            total_kwh = initial_kwh + charged_kwh
            
            return round(max(0, min(total_kwh, data['max_capacity'])), 2)
        except Exception as err:
            _LOGGER.debug("Error calculating SOC kWh: %s", err)
            return "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes about the calculation."""
        attrs = super().extra_state_attributes
        try:
            data = self._get_input_values()
            if self._is_valid_input(data):
                attrs.update({
                    "initial_soc_percent": data['initial_soc'],
                    "max_capacity_kwh": data['max_capacity'],
                    "energy_charged_kwh": data['energy_charged'],
                    "efficiency_correction": data['correction'],
                    "efficiency_factor": round((1 - data['correction'] / 100), 3)
                })
        except Exception as err:
            _LOGGER.debug("Error adding extra attributes: %s", err)
        return attrs

class EVSocPercentSensor(BaseEveusSensor):
    """EV State of Charge percentage sensor."""
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:battery-charging"
    name = "EV State of Charge"

    @property
    def native_value(self) -> float | str:
        """Return the state of charge percentage."""
        try:
            current_kwh = float(self.hass.states.get("sensor.ev_soc_kwh").state)
            max_capacity = float(self.hass.states.get("input_number.ev_battery_capacity").state)
            
            if current_kwh >= 0 and max_capacity > 0:
                percentage = round((current_kwh / max_capacity * 100), 0)
                return max(0, min(percentage, 100))
            return "unknown"
        except (AttributeError, TypeError, ValueError):
            return "unknown"

class TimeToTargetSocSensor(BaseEveusSensor):
    """Time to target SOC sensor."""
    _attr_icon = "mdi:timer"
    name = "Time to Target SOC"

    @property
    def native_value(self) -> str:
        """Calculate and return time to target SOC."""
        try:
            if self._updater.data.get("state") != 4:  # Not charging (4 is charging state)
                return "Not charging"

            soc = self.hass.states.get("sensor.ev_soc_percent")
            if soc is None or soc.state in ["unknown", "unavailable", "none"]:
                return "unknown"
            
            current_soc = float(soc.state)
            if current_soc < 0 or current_soc > 100:
                return "unknown"

            target_soc = float(self.hass.states.get("input_number.target_soc").state)
            if target_soc <= current_soc:
                return "Target reached"

            power_meas = float(self._updater.data.get("powerMeas", 0))
            if power_meas < 100:
                return "Insufficient power"

            battery_capacity = float(self.hass.states.get("input_number.ev_battery_capacity").state)
            correction = float(self.hass.states.get("input_number.ev_soc_correction").state)

            remaining_kwh = (target_soc - current_soc) * battery_capacity / 100
            power_kw = power_meas * (1 - correction / 100) / 1000
            total_minutes = round((remaining_kwh / power_kw * 60), 0)

            if total_minutes < 1:
                return "Less than 1m"
            elif total_minutes < 60:
                return f"{total_minutes}m"
            else:
                hours = int(total_minutes // 60)
                minutes = int(total_minutes % 60)
                if minutes > 0:
                    return f"{hours}h {minutes}m"
                return f"{hours}h"

        except (AttributeError, TypeError, ValueError) as err:
            _LOGGER.debug("Error calculating time to target: %s", err)
            return "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes about the calculation."""
        attrs = super().extra_state_attributes
        try:
            attrs["current_soc"] = self.hass.states.get("sensor.ev_soc_percent").state
            attrs["target_soc"] = self.hass.states.get("input_number.target_soc").state
            attrs["power"] = self._updater.data.get("powerMeas")
            attrs["battery_capacity"] = self.hass.states.get("input_number.ev_battery_capacity").state
            attrs["correction"] = self.hass.states.get("input_number.ev_soc_correction").state
        except (AttributeError, TypeError, ValueError):
            pass
        return attrs

class EveusCounterAEnergySensor(EveusEnergyBaseSensor):
    """Counter A energy sensor."""
    _key = ATTR_COUNTER_A_ENERGY
    _attr_icon = "mdi:counter"
    name = "Counter A Energy"

class EveusCounterBEnergySensor(EveusEnergyBaseSensor):
    """Counter B energy sensor."""
    _key = ATTR_COUNTER_B_ENERGY
    _attr_icon = "mdi:counter"
    name = "Counter B Energy"

class EveusCounterACostSensor(EveusNumericSensor):
    """Counter A cost sensor."""
    _attr_native_unit_of_measurement = "₴"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:currency-uah"
    _key = ATTR_COUNTER_A_COST
    name = "Counter A Cost"

class EveusCounterBCostSensor(EveusNumericSensor):
    """Counter B cost sensor."""
    _attr_native_unit_of_measurement = "₴"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:currency-uah"
    _key = ATTR_COUNTER_B_COST
    name = "Counter B Cost"

class EveusBatteryVoltageSensor(EveusNumericSensor):
    """Battery voltage sensor."""
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery"
    _key = ATTR_BATTERY_VOLTAGE
    name = "Battery Voltage"

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
        EveusBatteryVoltageSensor(updater),
        EVSocKwhSensor(updater),
        EVSocPercentSensor(updater),
        TimeToTargetSocSensor(updater),
    ]
    
    async_add_entities(entities)
    _LOGGER.debug("Added %s Eveus entities", len(entities))
