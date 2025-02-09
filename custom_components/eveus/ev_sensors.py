"""Support for Eveus EV-specific sensors."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy

from .common import EveusSensorBase
from .utils import get_safe_value, validate_required_values, calculate_remaining_time

_LOGGER = logging.getLogger(__name__)

class EVSocKwhSensor(EveusSensorBase):
    """Sensor for state of charge in kWh."""
    
    ENTITY_NAME = "SOC Energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:battery-charging"
    _attr_suggested_display_precision = 0
    _attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self) -> float | None:
        """Calculate and return state of charge in kWh."""
        try:
            initial_soc = get_safe_value(
                self.hass.states.get("input_number.ev_initial_soc")
            )
            max_capacity = get_safe_value(
                self.hass.states.get("input_number.ev_battery_capacity")
            )
            correction = get_safe_value(
                self.hass.states.get("input_number.ev_soc_correction")
            )
            energy_charged = get_safe_value(self._updater.data, "IEM1", default=0)

            if not validate_required_values(initial_soc, max_capacity, correction):
                _LOGGER.debug("Missing required values for SOC calculation")
                return None

            if initial_soc < 0 or initial_soc > 100 or max_capacity <= 0:
                _LOGGER.debug("Invalid values for SOC calculation")
                return None

            initial_kwh = (initial_soc / 100) * max_capacity
            efficiency = (1 - correction / 100)
            charged_kwh = energy_charged * efficiency
            total_kwh = initial_kwh + charged_kwh
            
            return round(max(0, min(total_kwh, max_capacity)), 2)

        except Exception as err:
            _LOGGER.error("Error calculating SOC in kWh: %s", err)
            return None

class EVSocPercentSensor(EveusSensorBase):
    """Sensor for state of charge percentage."""
    
    ENTITY_NAME = "SOC Percent"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:battery-charging"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    @property
    def native_value(self) -> float | None:
        """Return the state of charge percentage."""
        try:
            soc_kwh = get_safe_value(
                self.hass.states.get("sensor.eveus_ev_charger_soc_energy")
            )
            max_capacity = get_safe_value(
                self.hass.states.get("input_number.ev_battery_capacity")
            )
            
            if not validate_required_values(soc_kwh, max_capacity) or max_capacity <= 0:
                _LOGGER.debug("Missing or invalid values for SOC percentage calculation")
                return None

            percentage = round((soc_kwh / max_capacity * 100), 0)
            return max(0, min(percentage, 100))
        except Exception as err:
            _LOGGER.error("Error calculating SOC percentage: %s", err)
            return None

class TimeToTargetSocSensor(EveusSensorBase):
    """Time to target SOC sensor."""
    
    ENTITY_NAME = "Time to Target SOC"
    _attr_icon = "mdi:timer"

    @property
    def native_value(self) -> str:
        """Calculate and return formatted time to target."""
        try:
            current_soc = get_safe_value(
                self.hass.states.get("sensor.eveus_ev_charger_soc_percent")
            )
            target_soc = get_safe_value(
                self.hass.states.get("input_number.ev_target_soc")
            )
            battery_capacity = get_safe_value(
                self.hass.states.get("input_number.ev_battery_capacity")
            )
            correction = get_safe_value(
                self.hass.states.get("input_number.ev_soc_correction")
            )
            power_meas = get_safe_value(self._updater.data, "powerMeas", default=0)

            return calculate_remaining_time(
                current_soc,
                target_soc,
                power_meas,
                battery_capacity,
                correction
            )

        except Exception as err:
            _LOGGER.debug("Error calculating time to target: %s", err)
            return "unavailable"
