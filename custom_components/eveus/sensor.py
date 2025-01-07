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
        """Update the data with retry mechanism."""
        session = await self._get_session()
        try:
            async with session.post(
                f"http://{self._host}/main",
                auth=aiohttp.BasicAuth(self._username, self._password),
                timeout=10
            ) as response:
                response.raise_for_status()
                self._data = await response.json()
                self._available = True
                _LOGGER.debug(
                    "Updated data for %s: State=%s, Power=%sW", 
                    self._host,
                    self._data.get("state"), 
                    self._data.get("powerMeas")
                )
        except aiohttp.ClientError as err:
            self._available = False
            _LOGGER.error("Connection error for %s: %s", self._host, err)
            raise
        except Exception as err:
            self._available = False
            _LOGGER.error("Unexpected error for %s: %s", self._host, err)
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
        await super().async_will_remove_from_hass()
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
    """Base energy sensor with statistics."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

class EveusVoltageSensor(EveusNumericSensor):
    """Voltage sensor."""
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:flash"
    _key = ATTR_VOLTAGE
    name = "Voltage"

class EveusCurrentSensor(EveusNumericSensor):
    """Current sensor."""
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:current-ac"
    _key = ATTR_CURRENT
    name = "Current"

class EveusPowerSensor(EveusNumericSensor):
    """Power sensor."""
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:flash"
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

class EveusGroundSensor(BaseEveusSensor):
    """Ground sensor."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:ground"
    name = "Ground"

    @property
    def native_value(self) -> str:
        """Return ground status."""
        try:
            return "Yes" if self._updater.data[ATTR_GROUND] == 1 else "No"
        except (KeyError, TypeError):
            return "Unknown"

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
    _attr_icon = "mdi:clock"
    name = "System Time"

    @property
    def native_value(self) -> StateType:
        """Return system time."""
        try:
            return int(self._updater.data[self._key])
        except (KeyError, TypeError, ValueError):
            return None

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

class EveusSessionTimeSensor(BaseEveusSensor):
    """Session time sensor with proper formatting."""
    _attr_icon = "mdi:timer"
    _key = ATTR_SESSION_TIME
    name = "Session Time"

    @property
    def native_value(self) -> str:
        """Return formatted session time."""
        try:
            seconds = int(self._updater.data.get(self._key, 0))
            days = seconds // 86400
            hours = (seconds % 86400) // 3600
            minutes = (seconds % 3600) // 60
            
            if days > 0:
                return f"{days}d {hours:02d}h {minutes:02d}m"
            elif hours > 0:
                return f"{hours}h {minutes:02d}m"
            else:
                return f"{minutes}m"
        except (KeyError, TypeError, ValueError):
            return "unknown"

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return None as unit since we're returning formatted string."""
        return None

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
    ]
    
    async_add_entities(entities)
    _LOGGER.debug("Added %s Eveus entities", len(entities))
