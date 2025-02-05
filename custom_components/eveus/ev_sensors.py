"""Support for Eveus EV-specific sensors."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.text import TextEntity
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.components.text import TextEntity
from homeassistant.const import UnitOfEnergy

from .common import BaseEveusEntity, EveusSensorBase, EveusUpdater

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
            initial_soc = float(self.hass.states.get("input_number.ev_initial_soc").state)
            max_capacity = float(self.hass.states.get("input_number.ev_battery_capacity").state)
            energy_charged = float(self._updater.data.get("IEM1", 0))
            correction = float(self.hass.states.get("input_number.ev_soc_correction").state)

            if None in (initial_soc, max_capacity, energy_charged, correction):
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

        except (TypeError, ValueError, AttributeError) as err:
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
            soc_kwh = float(self.hass.states.get("sensor.eveus_ev_charger_soc_energy").state)
            max_capacity = float(self.hass.states.get("input_number.ev_battery_capacity").state)
            
            if None in (soc_kwh, max_capacity) or max_capacity <= 0:
                _LOGGER.debug("Missing or invalid values for SOC percentage calculation")
                return None

            percentage = round((soc_kwh / max_capacity * 100), 0)
            return max(0, min(percentage, 100))
        except (TypeError, ValueError, AttributeError) as err:
            _LOGGER.error("Error calculating SOC percentage: %s", err)
            return None

class TimeToTargetSocSensor(TextEntity, BaseEveusEntity):
    """Time to target SOC text entity."""
    
    ENTITY_NAME = "Time to Target SOC"
    _attr_icon = "mdi:timer"
    _attr_pattern = None
    _attr_mode = "text"

    @property
    def native_value(self) -> str:
        """Calculate and return formatted time to target."""
        try:
            # Get state values with validation
            current_soc = float(self.hass.states.get("sensor.eveus_ev_charger_soc_percent").state)
            target_soc = float(self.hass.states.get("input_number.ev_target_soc").state)
            power_meas = float(self._updater.data.get("powerMeas", 0))
            battery_capacity = float(self.hass.states.get("input_number.ev_battery_capacity").state)
            correction = float(self.hass.states.get("input_number.ev_soc_correction").state)

            # Validate inputs
            if any(x is None for x in [current_soc, target_soc, power_meas, battery_capacity, correction]):
                _LOGGER.debug("Missing required values for time to target calculation")
                return "Not charging"

            if power_meas <= 0:
                return "Not charging"

            # Calculate remaining energy needed
            remaining_kwh = (target_soc - current_soc) * battery_capacity / 100
            
            if remaining_kwh <= 0:
                return "Target reached"

            # Calculate time with efficiency correction
            efficiency = (1 - correction / 100)
            power_kw = power_meas * efficiency / 1000
            
            if power_kw <= 0:
                return "Not charging"

            # Calculate time components
            total_minutes = round((remaining_kwh / power_kw * 60), 0)
            
            if total_minutes < 1:
                return "< 1m"

            days = int(total_minutes // 1440)
            hours = int((total_minutes % 1440) // 60)
            minutes = int(total_minutes % 60)

            # Format time string with validation
            parts = []
            if days > 0:
                parts.append(f"{days}d")
            if hours > 0:
                parts.append(f"{hours}h")
            if minutes > 0 or not parts:
                parts.append(f"{minutes}m")

            return " ".join(parts)

        except (TypeError, ValueError, AttributeError) as err:
            _LOGGER.debug("Error calculating time to target: %s", err)
            return "Not charging"
        except Exception as err:
            _LOGGER.error("Unexpected error calculating time to target: %s", err)
            return "Error"
