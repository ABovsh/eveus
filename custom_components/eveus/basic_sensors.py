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

from .common import EveusSensorBase, EveusUpdater

_LOGGER = logging.getLogger(__name__)

class EveusVoltageSensor(EveusSensorBase):
    """Voltage measurement sensor."""
    
    ENTITY_NAME = "Voltage"
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:lightning-bolt"
    _attr_suggested_display_precision = 0

    @property
    def native_value(self) -> float | None:
        """Return voltage measurement."""
        try:
            value = self._updater.data.get("voltMeas1")
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError) as err:
            _LOGGER.error("Error getting voltage: %s", err)
            return None

class EveusCurrentSensor(EveusSensorBase):
    """Current measurement sensor."""
    
    ENTITY_NAME = "Current"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:current-ac"
    _attr_suggested_display_precision = 1

    @property
    def native_value(self) -> float | None:
        """Return current measurement."""
        try:
            value = self._updater.data.get("curMeas1")
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError) as err:
            _LOGGER.error("Error getting current: %s", err)
            return None

class EveusPowerSensor(EveusSensorBase):
    """Power measurement sensor."""
    
    ENTITY_NAME = "Power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:flash"
    _attr_suggested_display_precision = 0

    @property
    def native_value(self) -> float | None:
        """Return power measurement."""
        try:
            value = self._updater.data.get("powerMeas")
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError) as err:
            _LOGGER.error("Error getting power: %s", err)
            return None

class EveusCurrentSetSensor(EveusSensorBase):
    """Current set sensor."""

    ENTITY_NAME = "Current Set"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:current-ac"
    _attr_suggested_display_precision = 0

    @property
    def native_value(self) -> float | None:
        """Return current set value."""
        try:
            value = self._updater.data.get("currentSet")
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError) as err:
            _LOGGER.error("Error getting current set value: %s", err)
            return None

class EveusCurrentDesignedSensor(EveusSensorBase):
    """Current designed sensor."""

    ENTITY_NAME = "Current Designed"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:current-ac"
    _attr_suggested_display_precision = 0

    @property
    def native_value(self) -> float | None:
        """Return designed current value."""
        try:
            value = self._updater.data.get("curDesign")
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError) as err:
            _LOGGER.error("Error getting designed current: %s", err)
            return None

class EveusSessionTimeSensor(EveusSensorBase):
    """Session time sensor in seconds."""

    ENTITY_NAME = "Session Time"
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_icon = "mdi:timer"
    _attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self) -> int | None:
        """Return session time in seconds."""
        try:
            value = self._updater.data.get("sessionTime")
            if value is None:
                return None
            return int(value)
        except (TypeError, ValueError) as err:
            _LOGGER.error("Error getting session time: %s", err)
            return None

class EveusFormattedSessionTimeSensor(EveusSensorBase):
    """Formatted session time sensor."""

    ENTITY_NAME = "Session Duration"
    _attr_icon = "mdi:timer"

    @property
    def native_value(self) -> str | None:
        """Return formatted session time."""
        try:
            seconds = int(self._updater.data.get("sessionTime", 0))
            days = seconds // 86400
            hours = (seconds % 86400) // 3600
            minutes = (seconds % 3600) // 60
            
            if days > 0:
                return f"{days}d {hours}h {minutes}m"
            if hours > 0:
                return f"{hours}h {minutes}m"
            return f"{minutes}m"
        except (TypeError, ValueError) as err:
            _LOGGER.error("Error formatting session time: %s", err)
            return None

class EveusSessionEnergySensor(EveusSensorBase):
    """Session energy sensor."""
    
    ENTITY_NAME = "Session Energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL
    _attr_icon = "mdi:battery-charging"
    _attr_suggested_display_precision = 1

    @property
    def native_value(self) -> float | None:
        """Return session energy."""
        try:
            value = self._updater.data.get("sessionEnergy")
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError) as err:
            _LOGGER.error("Error getting session energy: %s", err)
            return None

class EveusTotalEnergySensor(EveusSensorBase):
    """Total energy sensor."""
    
    ENTITY_NAME = "Total Energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:battery-charging-100"
    _attr_suggested_display_precision = 1

    @property
    def native_value(self) -> float | None:
        """Return total energy."""
        try:
            value = self._updater.data.get("totalEnergy")
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError) as err:
            _LOGGER.error("Error getting total energy: %s", err)
            return None
