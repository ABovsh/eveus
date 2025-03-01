"""Support for Eveus EV-specific sensors."""
from __future__ import annotations

import logging
import traceback
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
    _attr_suggested_display_precision = 1
    _attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self) -> float | None:
        """Calculate and return state of charge in kWh."""
        _LOGGER.debug("SOC Energy sensor calculation started")
        try:
            # Check essential entities first
            state_obj = self.hass.states.get("input_number.ev_initial_soc")
            if state_obj is None:
                _LOGGER.warning("Entity input_number.ev_initial_soc does not exist yet, setup may still be in progress")
                return None
            
            capacity_obj = self.hass.states.get("input_number.ev_battery_capacity")
            if capacity_obj is None:
                _LOGGER.warning("Entity input_number.ev_battery_capacity does not exist yet, setup may still be in progress")
                return None
            
            correction_obj = self.hass.states.get("input_number.ev_soc_correction")
            if correction_obj is None:
                _LOGGER.warning("Entity input_number.ev_soc_correction does not exist yet, setup may still be in progress")
                return None
            
            # Get input values with detailed logging - use explicit defaults for robustness
            try:
                initial_soc = float(state_obj.state)
                _LOGGER.debug("initial_soc value: %s", initial_soc)
            except (ValueError, TypeError):
                _LOGGER.warning("Invalid state for initial_soc: %s", state_obj.state)
                return None
                
            try:
                max_capacity = float(capacity_obj.state)
                _LOGGER.debug("max_capacity value: %s", max_capacity)
            except (ValueError, TypeError):
                _LOGGER.warning("Invalid state for max_capacity: %s", capacity_obj.state)
                return None
                
            try:
                correction = float(correction_obj.state)
                _LOGGER.debug("correction value: %s", correction)
            except (ValueError, TypeError):
                _LOGGER.warning("Invalid state for correction: %s", correction_obj.state)
                correction = 7.5  # Use a default value
                
            energy_charged = get_safe_value(self._updater.data, "IEM1", float, default=0)
            _LOGGER.debug("energy_charged (IEM1) value: %s", energy_charged)

            if initial_soc < 0 or initial_soc > 100 or max_capacity <= 0:
                _LOGGER.error("Invalid values for SOC calculation: initial_soc=%s, max_capacity=%s",
                              initial_soc, max_capacity)
                return None

            # Calculation details
            initial_kwh = (initial_soc / 100) * max_capacity
            _LOGGER.debug("Calculated initial energy: %s kWh", initial_kwh)
            
            efficiency = (1 - correction / 100)
            _LOGGER.debug("Calculated efficiency factor: %s", efficiency)
            
            charged_kwh = energy_charged * efficiency
            _LOGGER.debug("Calculated charged energy: %s kWh", charged_kwh)
            
            total_kwh = initial_kwh + charged_kwh
            _LOGGER.debug("Calculated total energy: %s kWh", total_kwh)
            
            result = round(max(0, min(total_kwh, max_capacity)), 2)
            _LOGGER.info("SOC Energy calculation result: %s kWh", result)
            return result

        except Exception as err:
            _LOGGER.error("Error calculating SOC in kWh: %s", err)
            _LOGGER.debug("Traceback: %s", traceback.format_exc())
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
        _LOGGER.debug("SOC Percent sensor calculation started")
        try:
            # First, check if initial_soc is available directly
            initial_soc_entity = self.hass.states.get("input_number.ev_initial_soc")
            if initial_soc_entity is None:
                _LOGGER.debug("Entity input_number.ev_initial_soc not available yet - returning default SOC")
                return 0  # Default to 0% rather than None to avoid "unknown" display
                
            # Check if we can use the SOC energy sensor
            energy_entity_id = "sensor.eveus_ev_charger_soc_energy"
            energy_state = self.hass.states.get(energy_entity_id)
            
            capacity_entity = self.hass.states.get("input_number.ev_battery_capacity")
            if capacity_entity is None:
                _LOGGER.debug("Entity input_number.ev_battery_capacity not available yet")
                
                # Use initial SOC directly if available
                try:
                    return float(initial_soc_entity.state)
                except (ValueError, TypeError):
                    _LOGGER.debug("Invalid initial_soc value: %s", initial_soc_entity.state)
                    return 0
            
            # Try to get battery capacity
            try:
                max_capacity = float(capacity_entity.state)
            except (ValueError, TypeError):
                _LOGGER.debug("Invalid battery capacity: %s", capacity_entity.state)
                return 0
            
            # Try to get SOC energy from sensor or calculate directly
            if energy_state and energy_state.state not in ('unknown', 'unavailable'):
                try:
                    soc_kwh = float(energy_state.state)
                    _LOGGER.debug("Using SOC Energy value: %s", soc_kwh)
                except (ValueError, TypeError):
                    _LOGGER.debug("Invalid SOC Energy value: %s", energy_state.state)
                    soc_kwh = None
            else:
                soc_kwh = None
                _LOGGER.debug("SOC Energy sensor not available or invalid")
            
            # If SOC energy is available, calculate percentage
            if soc_kwh is not None and max_capacity > 0:
                percentage = round((soc_kwh / max_capacity * 100), 0)
                result = max(0, min(percentage, 100))
                _LOGGER.debug("SOC Percent from energy: %s%%", result)
                return result
                
            # Fallback to direct calculation if needed
            _LOGGER.debug("Falling back to direct SOC calculation")
            try:
                initial_soc = float(initial_soc_entity.state)
            except (ValueError, TypeError):
                _LOGGER.debug("Invalid initial SOC: %s", initial_soc_entity.state)
                initial_soc = 0
                
            correction_entity = self.hass.states.get("input_number.ev_soc_correction")
            if correction_entity is None:
                _LOGGER.debug("SOC correction entity missing, using default")
                correction = 7.5  # Default value
            else:
                try:
                    correction = float(correction_entity.state)
                except (ValueError, TypeError):
                    correction = 7.5
            
            energy_charged = get_safe_value(self._updater.data, "IEM1", float, default=0)
            
            if max_capacity > 0:
                efficiency = (1 - correction / 100)
                charged_percent = (energy_charged * efficiency / max_capacity) * 100
                current_soc = initial_soc + charged_percent
                result = max(0, min(round(current_soc, 0), 100))
                _LOGGER.debug("Direct SOC calculation: %s%%", result)
                return result
            else:
                _LOGGER.debug("Invalid battery capacity, returning initial SOC")
                return initial_soc
            
        except Exception as err:
            _LOGGER.error("Error calculating SOC percentage: %s", err)
            _LOGGER.debug("Traceback: %s", traceback.format_exc())
            return 0  # Return 0 instead of None to avoid "unknown" state


class TimeToTargetSocSensor(EveusSensorBase):
    """Time to target SOC sensor."""
    
    ENTITY_NAME = "Time to Target SOC"
    _attr_icon = "mdi:timer"

    @property
    def native_value(self) -> str:
        """Calculate and return formatted time to target."""
        _LOGGER.debug("Time to Target SOC sensor calculation started")
        try:
            # Get current SOC - fallback to initial SOC if needed
            percent_entity_id = "sensor.eveus_ev_charger_soc_percent"
            percent_state = self.hass.states.get(percent_entity_id)
            
            if percent_state and percent_state.state not in ('unknown', 'unavailable'):
                try:
                    current_soc = float(percent_state.state)
                    _LOGGER.debug("Current SOC from sensor: %s%%", current_soc)
                except (ValueError, TypeError):
                    current_soc = None
            else:
                current_soc = None
            
            # If SOC sensor not available, try initial SOC
            if current_soc is None:
                initial_state = self.hass.states.get("input_number.ev_initial_soc")
                if initial_state:
                    try:
                        current_soc = float(initial_state.state)
                        _LOGGER.debug("Using initial SOC: %s%%", current_soc)
                    except (ValueError, TypeError):
                        current_soc = 0
                else:
                    current_soc = 0
                    _LOGGER.debug("Using default SOC: 0%")
            
            # Get target SOC
            target_state = self.hass.states.get("input_number.ev_target_soc")
            if not target_state:
                _LOGGER.debug("Target SOC entity missing")
                return "Unavailable"
            
            try:
                target_soc = float(target_state.state)
                _LOGGER.debug("Target SOC: %s%%", target_soc)
            except (ValueError, TypeError):
                _LOGGER.debug("Invalid target SOC: %s", target_state.state)
                return "Invalid target"
            
            # Get battery capacity
            capacity_state = self.hass.states.get("input_number.ev_battery_capacity")
            if not capacity_state:
                _LOGGER.debug("Battery capacity entity missing")
                return "Unavailable"
                
            try:
                battery_capacity = float(capacity_state.state)
                _LOGGER.debug("Battery capacity: %s kWh", battery_capacity)
            except (ValueError, TypeError):
                _LOGGER.debug("Invalid battery capacity: %s", capacity_state.state)
                return "Invalid capacity"
                
            # Get correction factor
            correction_state = self.hass.states.get("input_number.ev_soc_correction")
            if correction_state:
                try:
                    correction = float(correction_state.state)
                    _LOGGER.debug("Correction: %s%%", correction)
                except (ValueError, TypeError):
                    correction = 7.5
                    _LOGGER.debug("Using default correction: 7.5%%")
            else:
                correction = 7.5
                _LOGGER.debug("Using default correction: 7.5%%")
            
            # Get power
            power_meas = get_safe_value(self._updater.data, "powerMeas", float, default=0)
            _LOGGER.debug("Power: %s W", power_meas)
            
            if power_meas <= 0:
                return "Not charging"
                
            # Calculate time remaining
            remaining_kwh = ((target_soc - current_soc) * battery_capacity / 100)
            if remaining_kwh <= 0:
                return "Target reached"
                
            efficiency = (1 - correction / 100)
            power_kw = power_meas * efficiency / 1000
            
            total_minutes = round((remaining_kwh / power_kw * 60), 0)
            
            if total_minutes < 1:
                return "< 1m"
                
            # Format duration
            hours = int(total_minutes // 60)
            mins = int(total_minutes % 60)
            
            if hours > 0:
                result = f"{hours}h {mins:02d}m"
            else:
                result = f"{mins}m"
                
            _LOGGER.debug("Time to target calculation result: %s", result)
            return result

        except Exception as err:
            _LOGGER.error("Error calculating time to target: %s", err)
            _LOGGER.debug("Traceback: %s", traceback.format_exc())
            return "Unavailable"
