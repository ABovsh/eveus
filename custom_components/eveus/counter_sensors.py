"""Support for Eveus counter sensors."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy

from .common import EveusSensorBase
from .utils import get_safe_value

_LOGGER = logging.getLogger(__name__)

class EveusCounterAEnergySensor(EveusSensorBase):
    """Counter A energy sensor."""

    ENTITY_NAME = "Counter A Energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:counter"
    _attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float | None:
        """Return counter A energy."""
        try:
            value = get_safe_value(self._updater.data, "IEM1")
            if value is None:
                return None
            return round(value, 2)
        except Exception as err:
            _LOGGER.error("Error getting counter A energy: %s", err)
            return None

class EveusCounterACostSensor(EveusSensorBase):
    """Counter A cost sensor."""

    ENTITY_NAME = "Counter A Cost"
    _attr_native_unit_of_measurement = "₴"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:currency-uah"
    _attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float | None:
        """Return counter A cost."""
        try:
            value = get_safe_value(self._updater.data, "IEM1_money")
            if value is None:
                return None
            return round(value, 2)
        except Exception as err:
            _LOGGER.error("Error getting counter A cost: %s", err)
            return None

class EveusCounterBEnergySensor(EveusSensorBase):
    """Counter B energy sensor."""

    ENTITY_NAME = "Counter B Energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:counter"
    _attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float | None:
        """Return counter B energy."""
        try:
            value = get_safe_value(self._updater.data, "IEM2")
            if value is None:
                return None
            return round(value, 2)
        except Exception as err:
            _LOGGER.error("Error getting counter B energy: %s", err)
            return None

class EveusCounterBCostSensor(EveusSensorBase):
    """Counter B cost sensor."""

    ENTITY_NAME = "Counter B Cost"
    _attr_native_unit_of_measurement = "₴"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:currency-uah"
    _attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float | None:
        """Return counter B cost."""
        try:
            value = get_safe_value(self._updater.data, "IEM2_money")
            if value is None:
                return None
            return round(value, 2)
        except Exception as err:
            _LOGGER.error("Error getting counter B cost: %s", err)
            return None
