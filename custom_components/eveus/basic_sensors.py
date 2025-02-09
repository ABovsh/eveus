"""Support for Eveus basic measurement sensors."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfElectricPotential,
    UnitOfElectricCurrent,
    UnitOfPower,
    UnitOfEnergy,
    UnitOfTime,
)

from .common import EveusSensorBase
from .utils import get_safe_value, format_duration

_LOGGER = logging.getLogger(__name__)

class EveusBasicMeasurementSensor(EveusSensorBase):
    """Base class for basic measurement sensors."""
    
    DATA_KEY: str = None
    _attr_suggested_display_precision = 1
    
    @property
    def native_value(self) -> float | None:
        """Return sensor value."""
        if not self.DATA_KEY:
            raise NotImplementedError("DATA_KEY must be defined in child class")
        return get_safe_value(self._updater.data, self.DATA_KEY)

class EveusVoltageSensor(EveusBasicMeasurementSensor):
    """Voltage measurement sensor."""
    
    ENTITY_NAME = "Voltage"
    DATA_KEY = "voltMeas1"
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:lightning-bolt"
    _attr_suggested_display_precision = 0

class EveusCurrentSensor(EveusBasicMeasurementSensor):
    """Current measurement sensor."""
    
    ENTITY_NAME = "Current"
    DATA_KEY = "curMeas1"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:current-ac"

class EveusPowerSensor(EveusBasicMeasurementSensor):
    """Power measurement sensor."""
    
    ENTITY_NAME = "Power"
    DATA_KEY = "powerMeas"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:flash"
    _attr_suggested_display_precision = 0

class EveusCurrentSetSensor(EveusBasicMeasurementSensor):
    """Current set sensor."""

    ENTITY_NAME = "Current Set"
    DATA_KEY = "currentSet"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:current-ac"
    _attr_suggested_display_precision = 0

class EveusSessionTimeSensor(EveusSensorBase):
    """Session time sensor."""
    
    ENTITY_NAME = "Session Time"
    _attr_icon = "mdi:timer"
    
    @property
    def native_value(self) -> str:
        """Return formatted session time."""
        seconds = get_safe_value(self._updater.data, "sessionTime", int, 0)
        return format_duration(seconds)

class EveusEnergySensorBase(EveusBasicMeasurementSensor):
    """Base energy sensor."""
    
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:battery-charging"
    _attr_suggested_display_precision = 1

class EveusSessionEnergySensor(EveusEnergySensorBase):
    """Session energy sensor."""
    
    ENTITY_NAME = "Session Energy"
    DATA_KEY = "sessionEnergy"
    _attr_state_class = SensorStateClass.TOTAL

class EveusTotalEnergySensor(EveusEnergySensorBase):
    """Total energy sensor."""
    
    ENTITY_NAME = "Total Energy"
    DATA_KEY = "totalEnergy"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:battery-charging-100"
