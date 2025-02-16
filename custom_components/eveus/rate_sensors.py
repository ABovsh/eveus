"""Support for Eveus rate sensors."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)

from .common import EveusSensorBase
from .const import RATE_STATES
from .utils import get_safe_value

_LOGGER = logging.getLogger(__name__)

class EveusRateSensorBase(EveusSensorBase):
    """Base class for rate sensors."""
    
    _attr_native_unit_of_measurement = "UAH/kWh"
    _attr_icon = "mdi:currency-uah"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, updater) -> None:
        """Initialize the sensor."""
        super().__init__(updater)
        self._attr_has_entity_name = True

class EveusPrimaryRateCostSensor(EveusRateSensorBase):
    """Primary rate cost sensor."""

    ENTITY_NAME = "Primary Rate Cost"

    @property
    def native_value(self) -> float | None:
        """Return the primary rate cost."""
        try:
            value = get_safe_value(self._updater.data, "tarif", float)
            if value is None:
                return None
            return round(value / 100, 2)
        except Exception as err:
            _LOGGER.error("Error getting primary rate cost: %s", err)
            return None

class EveusActiveRateCostSensor(EveusRateSensorBase):
    """Active rate cost sensor."""

    ENTITY_NAME = "Active Rate Cost"

    @property
    def native_value(self) -> float | None:
        """Return the active rate cost."""
        try:
            active_rate = get_safe_value(self._updater.data, "activeTarif", int)
            if active_rate is None:
                return None

            if active_rate == 0:
                value = get_safe_value(self._updater.data, "tarif", float)
            elif active_rate == 1:
                value = get_safe_value(self._updater.data, "tarifAValue", float)
            elif active_rate == 2:
                value = get_safe_value(self._updater.data, "tarifBValue", float)
            else:
                return None

            return round(value / 100, 2) if value is not None else None
        except Exception as err:
            _LOGGER.error("Error getting active rate cost: %s", err)
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return rate name."""
        try:
            active_rate = get_safe_value(self._updater.data, "activeTarif", int)
            if active_rate is not None:
                return {"rate_name": RATE_STATES.get(active_rate, "Unknown")}
        except Exception:
            pass
        return {}

class EveusRate2CostSensor(EveusRateSensorBase):
    """Rate 2 cost sensor."""

    ENTITY_NAME = "Rate 2 Cost"

    @property
    def native_value(self) -> float | None:
        """Return rate 2 cost."""
        try:
            value = get_safe_value(self._updater.data, "tarifAValue", float)
            if value is None:
                return None
            return round(value / 100, 2)
        except Exception as err:
            _LOGGER.error("Error getting rate 2 cost: %s", err)
            return None

class EveusRate3CostSensor(EveusRateSensorBase):
    """Rate 3 cost sensor."""

    ENTITY_NAME = "Rate 3 Cost"

    @property
    def native_value(self) -> float | None:
        """Return rate 3 cost."""
        try:
            value = get_safe_value(self._updater.data, "tarifBValue", float)
            if value is None:
                return None
            return round(value / 100, 2)
        except Exception as err:
            _LOGGER.error("Error getting rate 3 cost: %s", err)
            return None

class EveusRate2StatusSensor(EveusSensorBase):
    """Rate 2 status sensor."""

    ENTITY_NAME = "Rate 2 Status"
    _attr_icon = "mdi:clock-check"

    @property
    def native_value(self) -> str | None:
        """Return rate 2 status."""
        try:
            enabled = get_safe_value(self._updater.data, "tarifAEnable", int)
            if enabled is None:
                return None
            return "Enabled" if enabled == 1 else "Disabled"
        except Exception as err:
            _LOGGER.error("Error getting rate 2 status: %s", err)
            return None

class EveusRate3StatusSensor(EveusSensorBase):
    """Rate 3 status sensor."""

    ENTITY_NAME = "Rate 3 Status"
    _attr_icon = "mdi:clock-check"

    @property
    def native_value(self) -> str | None:
        """Return rate 3 status."""
        try:
            enabled = get_safe_value(self._updater.data, "tarifBEnable", int)
            if enabled is None:
                return None
            return "Enabled" if enabled == 1 else "Disabled"
        except Exception as err:
            _LOGGER.error("Error getting rate 3 status: %s", err)
            return None
