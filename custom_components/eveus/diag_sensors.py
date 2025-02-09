"""Support for Eveus diagnostic sensors."""
from __future__ import annotations

import logging
from typing import Any
import time
from datetime import datetime, timedelta
import pytz

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.components.sensor import SensorEntity
from homeassistant.const import UnitOfTemperature
from homeassistant.helpers.entity import EntityCategory

from .common import BaseEveusEntity
from .const import (
    CHARGING_STATES,
    ERROR_STATES,
    NORMAL_SUBSTATES,
)
from .utils import get_safe_value, is_dst

_LOGGER = logging.getLogger(__name__)

class EveusDiagnosticSensor(BaseEveusEntity, SensorEntity):
    """Base diagnostic sensor."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:information"
    DATA_KEY: str | None = None

    @property
    def native_value(self) -> Any | None:
        """Return sensor value."""
        if self.DATA_KEY:
            return get_safe_value(self._updater.data, self.DATA_KEY)
        return None

class EveusConnectionErrorsSensor(EveusDiagnosticSensor):
    """Failed requests counter sensor."""

    ENTITY_NAME = "Connection Errors"
    _attr_icon = "mdi:connection"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING 

    @property
    def native_value(self) -> int:
        """Return number of failed requests."""
        return self._updater.failed_requests

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional sensor state attributes."""
        return {
            "last_error_time": time.strftime(
                '%Y-%m-%d %H:%M:%S', 
                time.localtime(self._updater.last_error_time)
            ) if self._updater.last_error_time else None,
            "last_error_type": self._updater.last_error_type,
            "consecutive_errors": self._updater.consecutive_errors,
        }

class EveusStateSensor(EveusDiagnosticSensor):
    """Charging state sensor."""
    
    ENTITY_NAME = "State"
    DATA_KEY = "state"
    _attr_icon = "mdi:state-machine"

    @property
    def native_value(self) -> str:
        """Return charging state."""
        state_value = get_safe_value(self._updater.data, self.DATA_KEY, int)
        if state_value is not None:
            return CHARGING_STATES.get(state_value, "Unknown")
        return None

class EveusSubstateSensor(EveusDiagnosticSensor):
    """Charger substate sensor."""
    
    ENTITY_NAME = "Substate"
    _attr_icon = "mdi:information-variant"

    @property
    def native_value(self) -> str:
        """Return substate with context."""
        state = get_safe_value(self._updater.data, "state", int)
        substate = get_safe_value(self._updater.data, "subState", int)
        
        if None in (state, substate):
            return None
            
        if state == 7:  # Error state
            return ERROR_STATES.get(substate, "Unknown Error")
        return NORMAL_SUBSTATES.get(substate, "Unknown State")

class EveusGroundSensor(EveusDiagnosticSensor):
    """Ground connection sensor."""
    
    ENTITY_NAME = "Ground"
    DATA_KEY = "ground"
    _attr_icon = "mdi:electric-switch"

    @property
    def native_value(self) -> str:
        """Return ground status."""
        value = get_safe_value(self._updater.data, self.DATA_KEY, int)
        if value is not None:
            return "Connected" if value == 1 else "Not Connected"
        return None

class EveusTemperatureSensorBase(EveusDiagnosticSensor):
    """Base temperature sensor."""
    
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

class EveusBoxTemperatureSensor(EveusTemperatureSensorBase):
    """Box temperature sensor."""
    
    ENTITY_NAME = "Box Temperature"
    DATA_KEY = "temperature1"
    _attr_icon = "mdi:thermometer"

class EveusPlugTemperatureSensor(EveusTemperatureSensorBase):
    """Plug temperature sensor."""
    
    ENTITY_NAME = "Plug Temperature"
    DATA_KEY = "temperature2"
    _attr_icon = "mdi:thermometer-high"

class EveusBatteryVoltageSensor(EveusDiagnosticSensor):
    """Battery voltage sensor."""
    
    ENTITY_NAME = "Battery Voltage"
    DATA_KEY = "vBat"
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = "V"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery"
    _attr_suggested_display_precision = 2

class EveusSystemTimeSensor(EveusDiagnosticSensor):
    """System time sensor."""
    
    ENTITY_NAME = "System Time"
    DATA_KEY = "systemTime"
    _attr_icon = "mdi:clock-outline"

    @property
    def native_value(self) -> str:
        """Return timezone-corrected system time."""
        try:
            timestamp = get_safe_value(self._updater.data, self.DATA_KEY, int)
            if timestamp is None:
                return None
                
            # Get HA timezone
            ha_timezone = self.hass.config.time_zone
            if not ha_timezone:
                _LOGGER.warning("No timezone set in Home Assistant configuration")
                return None

            # Convert timestamp to datetime in UTC
            dt_utc = datetime.fromtimestamp(timestamp, tz=pytz.UTC)
            
            # Get local timezone
            local_tz = pytz.timezone(ha_timezone)
            
            # Check if we're in DST
            offset = 7200  # Base offset (2 hours)
            if is_dst(ha_timezone, dt_utc):
                offset += 3600  # Add 1 hour during DST
            
            # Apply correction
            corrected_timestamp = timestamp - offset
            dt_corrected = datetime.fromtimestamp(corrected_timestamp, tz=pytz.UTC)
            dt_local = dt_corrected.astimezone(local_tz)
            
            return dt_local.strftime("%H:%M")
                
        except Exception as err:
            _LOGGER.error("Error getting system time: %s", err)
            return None
