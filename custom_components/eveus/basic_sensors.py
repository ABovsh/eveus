"""Support for basic Eveus sensors."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
    UnitOfEnergy,
    UnitOfTime,
)

from .common import EveusSensorBase
from .utils import get_safe_value, format_duration

_LOGGER = logging.getLogger(__name__)

class EveusVoltageSensor(EveusSensorBase):
    """Voltage sensor."""

    ENTITY_NAME = "Voltage"
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    @property
    def native_value(self) -> float | None:
        """Return voltage."""
        try:
            value = get_safe_value(self._updater.data, "voltMeas1")
            if value is None:
                return None
            return round(value, 0)
        except Exception as err:
            _LOGGER.error("Error getting voltage: %s", err)
            return None

class EveusCurrentSensor(EveusSensorBase):
    """Current sensor."""

    ENTITY_NAME = "Current"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:current-ac"
    _attr_suggested_display_precision = 1

    @property
    def native_value(self) -> float | None:
        """Return current."""
        try:
            value = get_safe_value(self._updater.data, "curMeas1")
            if value is None:
                return None
            return round(value, 1)
        except Exception as err:
            _LOGGER.error("Error getting current: %s", err)
            return None

class EveusPowerSensor(EveusSensorBase):
    """Power sensor."""

    ENTITY_NAME = "Power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:flash"
    _attr_suggested_display_precision = 1

    @property
    def native_value(self) -> float | None:
        """Return power."""
        try:
            value = get_safe_value(self._updater.data, "powerMeas")
            if value is None:
                return None
            return round(value, 1)
        except Exception as err:
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
        """Return current set."""
        try:
            value = get_safe_value(self._updater.data, "currentSet")
            if value is None:
                return None
            return round(value, 0)
        except Exception as err:
            _LOGGER.error("Error getting current set: %s", err)
            return None

class EveusSessionTimeSensor(EveusSensorBase):
    """Session time sensor."""

    ENTITY_NAME = "Session Time"
    _attr_icon = "mdi:timer"

    @property
    def native_value(self) -> str | None:
        """Return formatted session time."""
        try:
            seconds = get_safe_value(self._updater.data, "sessionTime", int)
            if seconds is None:
                return None
            return format_duration(seconds)
        except Exception as err:
            _LOGGER.error("Error getting session time: %s", err)
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        try:
            seconds = get_safe_value(self._updater.data, "sessionTime", int)
            if seconds is not None:
                return {"duration_seconds": seconds}
        except Exception as err:
            _LOGGER.error("Error getting session time attributes: %s", err)
        return {}

class EveusSessionEnergySensor(EveusSensorBase):
    """Session energy sensor."""

    ENTITY_NAME = "Session Energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL
    _attr_icon = "mdi:transmission-tower-export"
    _attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float | None:
        """Return session energy."""
        try:
            value = get_safe_value(self._updater.data, "sessionEnergy")
            if value is None:
                return None
            return round(value, 2)
        except Exception as err:
            _LOGGER.error("Error getting session energy: %s", err)
            return None

class EveusTotalEnergySensor(EveusSensorBase):
    """Total energy sensor."""

    ENTITY_NAME = "Total Energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:transmission-tower"
    _attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float | None:
        """Return total energy."""
        try:
            value = get_safe_value(self._updater.data, "totalEnergy")
            if value is None:
                return None
            return round(value, 2)
        except Exception as err:
            _LOGGER.error("Error getting total energy: %s", err)
            return None
