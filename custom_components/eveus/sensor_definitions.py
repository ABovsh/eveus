"""Sensor definitions and factory for Eveus integration with stable offline handling."""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Type, Union
from datetime import datetime
from functools import lru_cache, partial
from dataclasses import dataclass
from enum import Enum

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.helpers.entity import EntityCategory
from homeassistant.core import HomeAssistant, callback, Event
from homeassistant.helpers.event import async_track_state_change_event
import pytz

from .common import EveusSensorBase
from .const import (
    get_charging_state,
    get_error_state,
    get_normal_substate,
    RATE_STATES,
    ERROR_LOG_RATE_LIMIT,
)
from .utils import (
    get_safe_value,
    is_dst,
    format_duration,
    calculate_remaining_time,
    validate_required_values,
)

_LOGGER = logging.getLogger(__name__)

# Phase 1: Global error logging control to reduce noise
_last_error_logs = {}

def _should_log_error(function_name: str) -> bool:
    """Phase 1: Check if we should log errors for a function (rate limited)."""
    current_time = time.time()
    last_log = _last_error_logs.get(function_name, 0)
    if current_time - last_log > ERROR_LOG_RATE_LIMIT:
        _last_error_logs[function_name] = current_time
        return True
    return False

class SensorType(Enum):
    """Sensor type enumeration for categorization."""
    MEASUREMENT = "measurement"
    ENERGY = "energy" 
    DIAGNOSTIC = "diagnostic"
    CALCULATED = "calculated"
    STATE = "state"

@dataclass(frozen=True)
class SensorSpec:
    """Immutable sensor specification for efficient sensor creation."""
    key: str
    name: str
    value_fn: Callable
    sensor_type: SensorType
    icon: Optional[str] = None
    device_class: Optional[str] = None
    state_class: Optional[str] = None
    unit: Optional[str] = None
    precision: Optional[int] = None
    category: Optional[EntityCategory] = None
    attributes_fn: Optional[Callable] = None
    
    def create_sensor(self, updater, device_number: int = 1) -> 'OptimizedEveusSensor':
        """Create sensor instance from specification."""
        return OptimizedEveusSensor(updater, self, device_number)

class OptimizedEveusSensor(EveusSensorBase):
    """High-performance templated sensor with stable offline handling."""
    
    def __init__(self, updater, spec: SensorSpec, device_number: int = 1):
        """Initialize optimized sensor."""
        self.ENTITY_NAME = spec.name
        super().__init__(updater, device_number)
        
        self._spec = spec
        self._cached_value = None
        self._cache_timestamp = 0
        self._cache_ttl = 30  # 30 seconds cache TTL
        
        # Apply spec attributes efficiently
        if spec.icon:
            self._attr_icon = spec.icon
        if spec.device_class:
            self._attr_device_class = spec.device_class
        if spec.state_class:
            self._attr_state_class = spec.state_class
        if spec.unit:
            self._attr_native_unit_of_measurement = spec.unit
        if spec.precision is not None:
            self._attr_suggested_display_precision = spec.precision
        if spec.category:
            self._attr_entity_category = spec.category

    def _get_sensor_value(self) -> Any:
        """Return cached or computed sensor value with proper offline handling.
        
        CRITICAL FIX: Clear cache and return None when updater is unavailable.
        This ensures sensors show as 'Unavailable' not 'Unknown' when offline.
        """
        # CRITICAL FIX: Check if updater is available first
        # If updater is unavailable, clear cache and return None
        # This makes the sensor properly show as "Unavailable" when device is offline
        if not self._updater.available:
            # Clear cache when device is offline to prevent stale data
            self._cached_value = None
            self._cache_timestamp = 0
            return None
        
        current_time = time.time()
        
        # Use cache for non-critical sensors only when device is available
        if (self._spec.sensor_type != SensorType.CALCULATED and 
            self._cached_value is not None and 
            current_time - self._cache_timestamp < self._cache_ttl):
            return self._cached_value
            
        try:
            value = self._spec.value_fn(self._updater, self.hass)
            
            # Only cache value if updater is available
            if self._updater.available and value is not None:
                self._cached_value = value
                self._cache_timestamp = current_time
            
            return value
        except Exception as err:
            if _should_log_error(f"sensor_{self._spec.key}"):
                _LOGGER.debug("Error getting value for %s: %s", self.name, err)
            # Don't return cached value when there's an error - return None
            # This helps sensors show as "Unavailable" when device is offline
            return None

    @property 
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return cached or computed attributes with stable error handling."""
        if self._spec.attributes_fn:
            try:
                # Don't return attributes when unavailable
                if not self._updater.available:
                    return {}
                return self._spec.attributes_fn(self._updater, self.hass)
            except Exception as err:
                if _should_log_error(f"attributes_{self._spec.key}"):
                    _LOGGER.debug("Error getting attributes for %s: %s", self.name, err)
        return {}

# Phase 1: Enhanced value functions with cached data fallback
def _get_data_value(updater, key: str, converter=float, default=None):
    """Phase 1: Helper to get value from current or cached data with offline awareness."""
    # CRITICAL FIX: When updater is unavailable, don't try to get values
    # This helps sensors show as "Unavailable" instead of "Unknown"
    if not updater.available:
        return None
    
    # Try current data first
    if updater.data and key in updater.data:
        return get_safe_value(updater.data, key, converter, default)
    
    # Try cached data from base entity only if updater was recently available
    entities = getattr(updater, '_entities', set())
    for entity in entities:
        if hasattr(entity, 'get_cached_data_value'):
            cached_value = entity.get_cached_data_value(key, default)
            if cached_value is not None:
                try:
                    return converter(cached_value)
                except (ValueError, TypeError):
                    pass
                    
    return default

# Enhanced value functions with stable error handling and caching
def get_voltage(updater, hass) -> float:
    """Get voltage with stable error handling and cached fallback."""
    try:
        value = _get_data_value(updater, "voltMeas1", float)
        return round(value, 0) if value is not None else None
    except Exception as err:
        if _should_log_error("get_voltage"):
            _LOGGER.debug("Error getting voltage: %s", err)
        return None

def get_current(updater, hass) -> float:
    """Get current with stable error handling and cached fallback."""
    try:
        value = _get_data_value(updater, "curMeas1", float)
        return round(value, 1) if value is not None else None
    except Exception as err:
        if _should_log_error("get_current"):
            _LOGGER.debug("Error getting current: %s", err)
        return None

def get_power(updater, hass) -> float: 
    """Get power with stable error handling and cached fallback."""
    try:
        value = _get_data_value(updater, "powerMeas", float)
        return round(value, 1) if value is not None else None
    except Exception as err:
        if _should_log_error("get_power"):
            _LOGGER.debug("Error getting power: %s", err)
        return None

def get_current_set(updater, hass) -> float:
    """Get current set with stable error handling and cached fallback."""
    try:
        value = _get_data_value(updater, "currentSet", float)
        return round(value, 0) if value is not None else None
    except Exception as err:
        if _should_log_error("get_current_set"):
            _LOGGER.debug("Error getting current set: %s", err)
        return None

def get_session_energy(updater, hass) -> float:
    """Get session energy with stable error handling and cached fallback."""
    try:
        value = _get_data_value(updater, "sessionEnergy", float)
        return round(value, 2) if value is not None else None
    except Exception as err:
        if _should_log_error("get_session_energy"):
            _LOGGER.debug("Error getting session energy: %s", err)
        return None

def get_total_energy(updater, hass) -> float:
    """Get total energy with stable error handling and cached fallback."""
    try:
        value = _get_data_value(updater, "totalEnergy", float)
        return round(value, 2) if value is not None else None
    except Exception as err:
        if _should_log_error("get_total_energy"):
            _LOGGER.debug("Error getting total energy: %s", err)
        return None

def get_counter_a_energy(updater, hass) -> float:
    """Get counter A energy with stable error handling and cached fallback."""
    try:
        value = _get_data_value(updater, "IEM1", float)
        return round(value, 2) if value is not None else None
    except Exception as err:
        if _should_log_error("get_counter_a_energy"):
            _LOGGER.debug("Error getting counter A energy: %s", err)
        return None

def get_counter_a_cost(updater, hass) -> float:
    """Get counter A cost with stable error handling and cached fallback."""
    try:
        value = _get_data_value(updater, "IEM1_money", float)
        return round(value, 2) if value is not None else None
    except Exception as err:
        if _should_log_error("get_counter_a_cost"):
            _LOGGER.debug("Error getting counter A cost: %s", err)
        return None

def get_counter_b_energy(updater, hass) -> float:
    """Get counter B energy with stable error handling and cached fallback."""
    try:
        value = _get_data_value(updater, "IEM2", float)
        return round(value, 2) if value is not None else None
    except Exception as err:
        if _should_log_error("get_counter_b_energy"):
            _LOGGER.debug("Error getting counter B energy: %s", err)
        return None

def get_counter_b_cost(updater, hass) -> float:
    """Get counter B cost with stable error handling and cached fallback."""
    try:
        value = _get_data_value(updater, "IEM2_money", float)
        return round(value, 2) if value is not None else None
    except Exception as err:
        if _should_log_error("get_counter_b_cost"):
            _LOGGER.debug("Error getting counter B cost: %s", err)
        return None

def get_box_temperature(updater, hass) -> float:
    """Get box temperature with stable error handling and cached fallback."""
    try:
        value = _get_data_value(updater, "temperature1", float)
        return round(value, 0) if value is not None else None
    except Exception as err:
        if _should_log_error("get_box_temperature"):
            _LOGGER.debug("Error getting box temperature: %s", err)
        return None

def get_plug_temperature(updater, hass) -> float:
    """Get plug temperature with stable error handling and cached fallback."""
    try:
        value = _get_data_value(updater, "temperature2", float)
        return round(value, 0) if value is not None else None
    except Exception as err:
        if _should_log_error("get_plug_temperature"):
            _LOGGER.debug("Error getting plug temperature: %s", err)
        return None

def get_battery_voltage(updater, hass) -> float:
    """Get battery voltage with stable error handling and cached fallback."""
    try:
        value = _get_data_value(updater, "vBat", float)
        return round(value, 2) if value is not None else None
    except Exception as err:
        if _should_log_error("get_battery_voltage"):
            _LOGGER.debug("Error getting battery voltage: %s", err)
        return None

# State functions with stable error handling and cached fallback
def get_charger_state(updater, hass) -> str:
    """Get charger state with stable error handling and cached fallback."""
    try:
        state_value = _get_data_value(updater, "state", int)
        return get_charging_state(state_value) if state_value is not None else None
    except Exception as err:
        if _should_log_error("get_charger_state"):
            _LOGGER.debug("Error getting charger state: %s", err)
        return None

def get_charger_substate(updater, hass) -> str:
    """Get charger substate with stable error handling and cached fallback."""
    try:
        state = _get_data_value(updater, "state", int)
        substate = _get_data_value(updater, "subState", int)
        
        if None in (state, substate):
            return None
            
        if state == 7:  # Error state
            return get_error_state(substate)
        return get_normal_substate(substate)
    except Exception as err:
        if _should_log_error("get_charger_substate"):
            _LOGGER.debug("Error getting charger substate: %s", err)
        return None

def get_ground_status(updater, hass) -> str:
    """Get ground status with stable error handling and cached fallback."""
    try:
        value = _get_data_value(updater, "ground", int)
        return "Connected" if value == 1 else "Not Connected" if value == 0 else None
    except Exception as err:
        if _should_log_error("get_ground_status"):
            _LOGGER.debug("Error getting ground status: %s", err)
        return None

# Time and session functions with stable error handling and cached fallback
def get_session_time(updater, hass) -> str:
    """Get formatted session time with stable error handling and cached fallback."""
    try:
        seconds = _get_data_value(updater, "sessionTime", int)
        return format_duration(seconds) if seconds is not None else None
    except Exception as err:
        if _should_log_error("get_session_time"):
            _LOGGER.debug("Error getting session time: %s", err)
        return None

def get_session_time_attrs(updater, hass) -> dict:
    """Get session time attributes with stable error handling and cached fallback."""
    try:
        if not updater.available:
            return {}
        seconds = _get_data_value(updater, "sessionTime", int)
        return {"duration_seconds": seconds} if seconds is not None else {}
    except Exception as err:
        if _should_log_error("get_session_time_attrs"):
            _LOGGER.debug("Error getting session time attributes: %s", err)
        return {}

def get_system_time(updater, hass) -> str:
    """Get system time with timezone correction, stable error handling and cached fallback."""
    try:
        timestamp = _get_data_value(updater, "systemTime", int)
        if timestamp is None:
            return None
            
        # Get HA timezone
        ha_timezone = hass.config.time_zone
        if not ha_timezone:
            return None

        # Convert with timezone handling
        dt_utc = datetime.fromtimestamp(timestamp, tz=pytz.UTC)
        local_tz = pytz.timezone(ha_timezone)
        
        # Apply DST correction - pass timestamp
        offset = 7200  # Base offset
        if is_dst(ha_timezone, timestamp):
            offset += 3600
        
        corrected_timestamp = timestamp - offset
        dt_corrected = datetime.fromtimestamp(corrected_timestamp, tz=pytz.UTC)
        dt_local = dt_corrected.astimezone(local_tz)
        
        return dt_local.strftime("%H:%M")
            
    except Exception as err:
        if _should_log_error("get_system_time"):
            _LOGGER.debug("Error getting system time: %s", err)
        return None

# Rate and cost functions with stable error handling and cached fallback
def get_primary_rate_cost(updater, hass) -> float:
    """Get primary rate cost with stable error handling and cached fallback."""
    try:
        value = _get_data_value(updater, "tarif", float)
        return round(value / 100, 2) if value is not None else None
    except Exception as err:
        if _should_log_error("get_primary_rate_cost"):
            _LOGGER.debug("Error getting primary rate cost: %s", err)
        return None

def get_active_rate_cost(updater, hass) -> float:
    """Get active rate cost with stable error handling and cached fallback."""
    try:
        active_rate = _get_data_value(updater, "activeTarif", int)
        if active_rate is None:
            return None

        # Use mapping for efficiency
        rate_keys = {0: "tarif", 1: "tarifAValue", 2: "tarifBValue"}
        key = rate_keys.get(active_rate)
        if not key:
            return None
            
        value = _get_data_value(updater, key, float)
        return round(value / 100, 2) if value is not None else None
    except Exception as err:
        if _should_log_error("get_active_rate_cost"):
            _LOGGER.debug("Error getting active rate cost: %s", err)
        return None

def get_active_rate_attrs(updater, hass) -> dict:
    """Get active rate attributes with stable error handling and cached fallback."""
    try:
        if not updater.available:
            return {}
        active_rate = _get_data_value(updater, "activeTarif", int)
        return {"rate_name": RATE_STATES.get(active_rate, "Unknown")} if active_rate is not None else {}
    except Exception as err:
        if _should_log_error("get_active_rate_attrs"):
            _LOGGER.debug("Error getting active rate attributes: %s", err)
        return {}

def get_rate2_cost(updater, hass) -> float:
    """Get rate 2 cost with stable error handling and cached fallback."""
    try:
        value = _get_data_value(updater, "tarifAValue", float)
        return round(value / 100, 2) if value is not None else None
    except Exception as err:
        if _should_log_error("get_rate2_cost"):
            _LOGGER.debug("Error getting rate 2 cost: %s", err)
        return None

def get_rate3_cost(updater, hass) -> float:
    """Get rate 3 cost with stable error handling and cached fallback."""
    try:
        value = _get_data_value(updater, "tarifBValue", float)
        return round(value / 100, 2) if value is not None else None
    except Exception as err:
        if _should_log_error("get_rate3_cost"):
            _LOGGER.debug("Error getting rate 3 cost: %s", err)
        return None

def get_rate_status(rate_key: str):
    """Factory function for rate status sensors with stable error handling and cached fallback."""
    def _get_rate_status(updater, hass) -> str:
        try:
            enabled = _get_data_value(updater, rate_key, int)
            return "Enabled" if enabled == 1 else "Disabled" if enabled == 0 else None
        except Exception as err:
            if _should_log_error(f"get_rate_status_{rate_key}"):
                _LOGGER.debug("Error getting rate status for %s: %s", rate_key, err)
            return None
    return _get_rate_status

# Network quality functions with stable error handling
def get_connection_quality(updater, hass) -> float:
    """Get connection quality as numeric value with stable error handling."""
    try:
        if hasattr(updater, '_network') and hasattr(updater._network, 'connection_quality'):
            metrics = updater._network.connection_quality
            return round(max(0, min(100, metrics.get('success_rate', 0))))
        return 100  # Default to 100% if no metrics available
    except Exception as err:
        if _should_log_error("get_connection_quality"):
            _LOGGER.debug("Error getting connection quality: %s", err)
        return 0

def get_connection_attrs(updater, hass) -> dict:
    """Get optimized connection attributes with stable error handling."""
    try:
        if not updater.available:
            return {}
            
        if not hasattr(updater, '_network'):
            return {"status": "Unknown"}
            
        metrics = updater._network.connection_quality
        success_rate = metrics.get('success_rate', 100)
        
        # Simplified attributes for better performance
        return {
            "connection_quality": f"{round(success_rate)}%",
            "latency_avg": f"{max(0, metrics.get('latency_avg', 0)):.2f}s",
            "recent_errors": metrics.get('recent_errors', 0),
            "status": "Excellent" if success_rate > 95 else 
                    "Good" if success_rate > 80 else
                    "Fair" if success_rate > 60 else
                    "Poor" if success_rate > 30 else "Critical"
        }
        
    except Exception as err:
        if _should_log_error("get_connection_attrs"):
            _LOGGER.debug("Error getting connection attributes: %s", err)
        return {"status": "Error"}

# Sensor specification factory - creates all sensors efficiently
def create_sensor_specifications() -> List[SensorSpec]:
    """Create all sensor specifications efficiently using factory pattern."""
    
    # Measurement sensors - created programmatically
    measurements = [
        ("Voltage", get_voltage, "mdi:flash", SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT, 0),
        ("Current", get_current, "mdi:current-ac", SensorDeviceClass.CURRENT, UnitOfElectricCurrent.AMPERE, 1), 
        ("Power", get_power, "mdi:flash", SensorDeviceClass.POWER, UnitOfPower.WATT, 1),
        ("Current Set", get_current_set, "mdi:current-ac", SensorDeviceClass.CURRENT, UnitOfElectricCurrent.AMPERE, 0),
    ]
    
    measurement_specs = [
        SensorSpec(
            key=name.lower().replace(" ", "_"),
            name=name,
            value_fn=fn,
            sensor_type=SensorType.MEASUREMENT,
            icon=icon,
            device_class=device_class,
            state_class=SensorStateClass.MEASUREMENT,
            unit=unit,
            precision=precision
        ) for name, fn, icon, device_class, unit, precision in measurements
    ]
    
    # Energy sensors - created programmatically
    energy_sensors = [
        ("Session Energy", get_session_energy, "mdi:transmission-tower-export", SensorStateClass.TOTAL),
        ("Total Energy", get_total_energy, "mdi:transmission-tower", SensorStateClass.TOTAL_INCREASING),
        ("Counter A Energy", get_counter_a_energy, "mdi:counter", SensorStateClass.TOTAL_INCREASING),
        ("Counter B Energy", get_counter_b_energy, "mdi:counter", SensorStateClass.TOTAL_INCREASING),
    ]
    
    energy_specs = [
        SensorSpec(
            key=name.lower().replace(" ", "_"),
            name=name,
            value_fn=fn,
            sensor_type=SensorType.ENERGY,
            icon=icon,
            device_class=SensorDeviceClass.ENERGY,
            state_class=state_class,
            unit=UnitOfEnergy.KILO_WATT_HOUR,
            precision=2
        ) for name, fn, icon, state_class in energy_sensors
    ]
    
    # Diagnostic sensors - create individually for clarity
    diagnostic_specs = [
        # Simple diagnostic sensors
        SensorSpec(
            key="state",
            name="State",
            value_fn=get_charger_state,
            sensor_type=SensorType.DIAGNOSTIC,
            icon="mdi:state-machine",
            category=EntityCategory.DIAGNOSTIC
        ),
        SensorSpec(
            key="substate",
            name="Substate",
            value_fn=get_charger_substate,
            sensor_type=SensorType.DIAGNOSTIC,
            icon="mdi:information-variant",
            category=EntityCategory.DIAGNOSTIC
        ),
        SensorSpec(
            key="ground",
            name="Ground",
            value_fn=get_ground_status,
            sensor_type=SensorType.DIAGNOSTIC,
            icon="mdi:electric-switch",
            category=EntityCategory.DIAGNOSTIC
        ),
        SensorSpec(
            key="system_time",
            name="System Time",
            value_fn=get_system_time,
            sensor_type=SensorType.DIAGNOSTIC,
            icon="mdi:clock-outline",
            category=EntityCategory.DIAGNOSTIC
        ),
        # Temperature sensors with device class
        SensorSpec(
            key="box_temperature",
            name="Box Temperature",
            value_fn=get_box_temperature,
            sensor_type=SensorType.DIAGNOSTIC,
            icon="mdi:thermometer",
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            unit=UnitOfTemperature.CELSIUS,
            precision=0,
            category=EntityCategory.DIAGNOSTIC
        ),
        SensorSpec(
            key="plug_temperature",
            name="Plug Temperature",
            value_fn=get_plug_temperature,
            sensor_type=SensorType.DIAGNOSTIC,
            icon="mdi:thermometer-high",
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            unit=UnitOfTemperature.CELSIUS,
            precision=0,
            category=EntityCategory.DIAGNOSTIC
        ),
        SensorSpec(
            key="battery_voltage",
            name="Battery Voltage",
            value_fn=get_battery_voltage,
            sensor_type=SensorType.DIAGNOSTIC,
            icon="mdi:battery",
            device_class=SensorDeviceClass.VOLTAGE,
            state_class=SensorStateClass.MEASUREMENT,
            unit="V",
            precision=2,
            category=EntityCategory.DIAGNOSTIC
        ),
    ]
    
    # Special sensors with individual specifications
    special_specs = [
        SensorSpec(
            key="session_time",
            name="Session Time", 
            value_fn=get_session_time,
            sensor_type=SensorType.STATE,
            icon="mdi:timer",
            attributes_fn=get_session_time_attrs
        ),
        SensorSpec(
            key="counter_a_cost",
            name="Counter A Cost",
            value_fn=get_counter_a_cost,
            sensor_type=SensorType.ENERGY,
            icon="mdi:currency-uah",
            state_class=SensorStateClass.TOTAL_INCREASING,
            unit="₴",
            precision=2
        ),
        SensorSpec(
            key="counter_b_cost", 
            name="Counter B Cost",
            value_fn=get_counter_b_cost,
            sensor_type=SensorType.ENERGY,
            icon="mdi:currency-uah",
            state_class=SensorStateClass.TOTAL_INCREASING,
            unit="₴",
            precision=2
        ),
        SensorSpec(
            key="primary_rate_cost",
            name="Primary Rate Cost",
            value_fn=get_primary_rate_cost,
            sensor_type=SensorType.STATE,
            icon="mdi:currency-uah",
            state_class=SensorStateClass.MEASUREMENT,
            unit="₴/kWh",
            precision=2
        ),
        SensorSpec(
            key="active_rate_cost",
            name="Active Rate Cost", 
            value_fn=get_active_rate_cost,
            sensor_type=SensorType.STATE,
            icon="mdi:currency-uah",
            state_class=SensorStateClass.MEASUREMENT,
            unit="₴/kWh",
            precision=2,
            attributes_fn=get_active_rate_attrs
        ),
        SensorSpec(
            key="rate_2_cost",
            name="Rate 2 Cost",
            value_fn=get_rate2_cost,
            sensor_type=SensorType.STATE,
            icon="mdi:currency-uah",
            state_class=SensorStateClass.MEASUREMENT,
            unit="₴/kWh", 
            precision=2
        ),
        SensorSpec(
            key="rate_3_cost",
            name="Rate 3 Cost",
            value_fn=get_rate3_cost,
            sensor_type=SensorType.STATE,
            icon="mdi:currency-uah",
            state_class=SensorStateClass.MEASUREMENT,
            unit="₴/kWh",
            precision=2
        ),
        SensorSpec(
            key="rate_2_status",
            name="Rate 2 Status",
            value_fn=get_rate_status("tarifAEnable"),
            sensor_type=SensorType.STATE,
            icon="mdi:clock-check"
        ),
        SensorSpec(
            key="rate_3_status", 
            name="Rate 3 Status",
            value_fn=get_rate_status("tarifBEnable"),
            sensor_type=SensorType.STATE,
            icon="mdi:clock-check"
        ),
        SensorSpec(
            key="connection_quality",
            name="Connection Quality",
            value_fn=get_connection_quality,
            sensor_type=SensorType.DIAGNOSTIC,
            icon="mdi:connection",
            state_class=SensorStateClass.MEASUREMENT,
            unit="%",
            precision=0,
            category=EntityCategory.DIAGNOSTIC,
            attributes_fn=get_connection_attrs
        ),
    ]
    
    return measurement_specs + energy_specs + diagnostic_specs + special_specs

def get_sensor_specifications() -> List[SensorSpec]:
    """Get all sensor specifications (cached for performance)."""
    return create_sensor_specifications()
