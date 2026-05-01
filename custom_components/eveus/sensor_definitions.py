"""Sensor definitions and factory for Eveus integration."""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, ClassVar, Dict, List, Optional
from datetime import datetime, timezone
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from zoneinfo import ZoneInfo

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.core import callback
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.helpers.entity import EntityCategory

from .common import EveusSensorBase
from .const import (
    get_charging_state,
    get_error_state,
    get_normal_substate,
    RATE_STATES,
    ERROR_LOG_RATE_LIMIT,
)
from .utils import get_safe_value, is_dst, format_duration

_LOGGER = logging.getLogger(__name__)


@lru_cache(maxsize=16)
def _get_zone_info(timezone_name: str) -> ZoneInfo:
    """Return cached timezone info for system-time conversion."""
    return ZoneInfo(timezone_name)


class SensorType(Enum):
    """Sensor type enumeration."""
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

    def create_sensor(self, updater, device_number: int = 1) -> "OptimizedEveusSensor":
        """Create sensor instance from specification."""
        return OptimizedEveusSensor(updater, self, device_number)


class OptimizedEveusSensor(EveusSensorBase):
    """High-performance templated sensor."""

    _last_error_logs: ClassVar[Dict[str, float]] = {}

    @classmethod
    def _should_log_error(cls, function_name: str) -> bool:
        """Check if we should log errors for a function (rate limited)."""
        current_time = time.time()
        last_log = cls._last_error_logs.get(function_name, 0)
        if current_time - last_log > ERROR_LOG_RATE_LIMIT:
            cls._last_error_logs[function_name] = current_time
            return True
        return False

    def __init__(self, updater, spec: SensorSpec, device_number: int = 1):
        """Initialize sensor from spec."""
        self.ENTITY_NAME = spec.name
        super().__init__(updater, device_number)

        self._spec = spec

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
        """Return computed sensor value from coordinator data."""
        if not self._updater.available:
            return None

        try:
            return self._spec.value_fn(self._updater, self.hass)
        except Exception as err:
            if self._should_log_error(f"sensor_{self._spec.key}"):
                _LOGGER.debug("Error getting value for %s: %s", self.name, err, exc_info=True)
            return None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle fresh coordinator data."""
        availability_changed = self._update_availability_state()
        value_changed = self._update_native_value()
        if availability_changed or value_changed:
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return attributes."""
        if self._spec.attributes_fn:
            try:
                if not self._updater.available:
                    return {}
                return self._spec.attributes_fn(self._updater, self.hass)
            except Exception as err:
                if self._should_log_error(f"attributes_{self._spec.key}"):
                    _LOGGER.debug(
                        "Error getting attributes for %s: %s",
                        self.name,
                        err,
                        exc_info=True,
                    )
        return {}


# =============================================================================
# Value helper
# =============================================================================

def _get_data_value(updater, key: str, converter=float, default=None):
    """Get value from updater data. Returns None when offline."""
    if not updater.available or not updater.data:
        return None
    if key in updater.data:
        return get_safe_value(updater.data, key, converter, default)
    return default


# =============================================================================
# Value getter factories — replace ~20 identical functions
# =============================================================================

def _make_value_getter(key: str, precision: int = 0, transform: Callable = None):
    """Factory for simple data getter functions."""
    def getter(updater, hass):
        value = _get_data_value(updater, key)
        if value is None:
            return None
        if transform:
            value = transform(value)
        return round(value, precision)
    return getter


# Measurement getters
get_voltage = _make_value_getter("voltMeas1", precision=0)
get_current = _make_value_getter("curMeas1", precision=1)
get_power = _make_value_getter("powerMeas", precision=1)
get_current_set = _make_value_getter("currentSet", precision=0)

# Energy getters
get_session_energy = _make_value_getter("sessionEnergy", precision=2)
get_total_energy = _make_value_getter("totalEnergy", precision=2)
get_counter_a_energy = _make_value_getter("IEM1", precision=2)
get_counter_b_energy = _make_value_getter("IEM2", precision=2)

# Cost getters (divide by 100)
_div100 = lambda v: v / 100
get_counter_a_cost = _make_value_getter("IEM1_money", precision=2)
get_counter_b_cost = _make_value_getter("IEM2_money", precision=2)
get_primary_rate_cost = _make_value_getter("tarif", precision=2, transform=_div100)
get_rate2_cost = _make_value_getter("tarifAValue", precision=2, transform=_div100)
get_rate3_cost = _make_value_getter("tarifBValue", precision=2, transform=_div100)

# Temperature getters
get_box_temperature = _make_value_getter("temperature1", precision=0)
get_plug_temperature = _make_value_getter("temperature2", precision=0)

# Other diagnostic getters
get_battery_voltage = _make_value_getter("vBat", precision=2)


# =============================================================================
# State-based getters (need custom logic)
# =============================================================================

def get_charger_state(updater, hass) -> Optional[str]:
    """Get charger state."""
    state_value = _get_data_value(updater, "state", int)
    return get_charging_state(state_value) if state_value is not None else None


def get_charger_substate(updater, hass) -> Optional[str]:
    """Get charger substate."""
    state = _get_data_value(updater, "state", int)
    substate = _get_data_value(updater, "subState", int)
    if None in (state, substate):
        return None
    if state == 7:
        return get_error_state(substate)
    return get_normal_substate(substate)


def get_ground_status(updater, hass) -> Optional[str]:
    """Get ground status."""
    value = _get_data_value(updater, "ground", int)
    if value == 1:
        return "Connected"
    if value == 0:
        return "Not Connected"
    return None


def get_session_time(updater, hass) -> Optional[str]:
    """Get formatted session time."""
    seconds = _get_data_value(updater, "sessionTime", int)
    return format_duration(seconds) if seconds is not None else None


def get_session_time_attrs(updater, hass) -> dict:
    """Get session time attributes."""
    if not updater.available:
        return {}
    seconds = _get_data_value(updater, "sessionTime", int)
    return {"duration_seconds": seconds} if seconds is not None else {}


def get_system_time(updater, hass) -> Optional[str]:
    """Get system time with timezone correction."""
    try:
        timestamp = _get_data_value(updater, "systemTime", int)
        if timestamp is None:
            return None

        ha_timezone = hass.config.time_zone
        if not ha_timezone:
            return None

        offset = 7200
        if is_dst(ha_timezone, timestamp):
            offset += 3600

        corrected_timestamp = timestamp - offset
        dt_corrected = datetime.fromtimestamp(corrected_timestamp, tz=timezone.utc)
        local_tz = _get_zone_info(ha_timezone)
        dt_local = dt_corrected.astimezone(local_tz)
        return dt_local.strftime("%H:%M")

    except Exception as err:
        if OptimizedEveusSensor._should_log_error("get_system_time"):
            _LOGGER.debug("Error getting system time: %s", err, exc_info=True)
        return None


def get_active_rate_cost(updater, hass) -> Optional[float]:
    """Get active rate cost."""
    active_rate = _get_data_value(updater, "activeTarif", int)
    if active_rate is None:
        return None
    rate_keys = {0: "tarif", 1: "tarifAValue", 2: "tarifBValue"}
    key = rate_keys.get(active_rate)
    if not key:
        return None
    value = _get_data_value(updater, key)
    return round(value / 100, 2) if value is not None else None


def get_active_rate_attrs(updater, hass) -> dict:
    """Get active rate attributes."""
    if not updater.available:
        return {}
    active_rate = _get_data_value(updater, "activeTarif", int)
    return {"rate_name": RATE_STATES.get(active_rate, "Unknown")} if active_rate is not None else {}


def _make_rate_status_getter(rate_key: str):
    """Factory for rate status sensors."""
    def getter(updater, hass) -> Optional[str]:
        enabled = _get_data_value(updater, rate_key, int)
        if enabled == 1:
            return "Enabled"
        if enabled == 0:
            return "Disabled"
        return None
    return getter


# =============================================================================
# Connection quality
# =============================================================================

def get_connection_quality(updater, hass) -> float:
    """Get connection quality as numeric value."""
    try:
        metrics = updater.connection_quality
        return round(max(0, min(100, metrics.get("success_rate", 0))))
    except Exception as err:
        if OptimizedEveusSensor._should_log_error("get_connection_quality"):
            _LOGGER.debug("Error getting connection quality: %s", err, exc_info=True)
        return 100


def get_connection_attrs(updater, hass) -> dict:
    """Get connection attributes."""
    try:
        if not updater.available:
            return {}
        metrics = updater.connection_quality
        success_rate = metrics.get("success_rate", 100)
        return {
            "connection_quality": f"{round(success_rate)}%",
            "latency_avg": f"{max(0, metrics.get('latency_avg', 0)):.2f}s",
            "status": (
                "Excellent" if success_rate > 95 else
                "Good" if success_rate > 80 else
                "Fair" if success_rate > 60 else
                "Poor" if success_rate > 30 else "Critical"
            ),
        }
    except Exception as err:
        if OptimizedEveusSensor._should_log_error("get_connection_attrs"):
            _LOGGER.debug("Error getting connection attributes: %s", err, exc_info=True)
        return {"status": "Error"}


# =============================================================================
# Sensor specification factory
# =============================================================================

def create_sensor_specifications() -> List[SensorSpec]:
    """Create all sensor specifications using factory pattern."""

    # Measurement sensors
    measurements = [
        ("Voltage", get_voltage, "mdi:flash", SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT, 0, None),
        ("Current", get_current, "mdi:current-ac", SensorDeviceClass.CURRENT, UnitOfElectricCurrent.AMPERE, 1, None),
        ("Power", get_power, "mdi:flash", SensorDeviceClass.POWER, UnitOfPower.WATT, 1, None),
        (
            "Current Set",
            get_current_set,
            "mdi:current-ac",
            SensorDeviceClass.CURRENT,
            UnitOfElectricCurrent.AMPERE,
            0,
            EntityCategory.DIAGNOSTIC,
        ),
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
            precision=precision,
            category=category,
        )
        for name, fn, icon, device_class, unit, precision, category in measurements
    ]

    # Energy sensors
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
            precision=2,
        )
        for name, fn, icon, state_class in energy_sensors
    ]

    # Diagnostic sensors
    diagnostic_specs = [
        SensorSpec(
            key="state", name="State", value_fn=get_charger_state,
            sensor_type=SensorType.DIAGNOSTIC, icon="mdi:state-machine",
            category=EntityCategory.DIAGNOSTIC,
        ),
        SensorSpec(
            key="substate", name="Substate", value_fn=get_charger_substate,
            sensor_type=SensorType.DIAGNOSTIC, icon="mdi:information-variant",
            category=EntityCategory.DIAGNOSTIC,
        ),
        SensorSpec(
            key="ground", name="Ground", value_fn=get_ground_status,
            sensor_type=SensorType.DIAGNOSTIC, icon="mdi:electric-switch",
            category=EntityCategory.DIAGNOSTIC,
        ),
        SensorSpec(
            key="system_time", name="System Time", value_fn=get_system_time,
            sensor_type=SensorType.DIAGNOSTIC, icon="mdi:clock-outline",
            category=EntityCategory.DIAGNOSTIC,
        ),
        SensorSpec(
            key="box_temperature", name="Box Temperature", value_fn=get_box_temperature,
            sensor_type=SensorType.DIAGNOSTIC, icon="mdi:thermometer",
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            unit=UnitOfTemperature.CELSIUS, precision=0,
            category=EntityCategory.DIAGNOSTIC,
        ),
        SensorSpec(
            key="plug_temperature", name="Plug Temperature", value_fn=get_plug_temperature,
            sensor_type=SensorType.DIAGNOSTIC, icon="mdi:thermometer-high",
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            unit=UnitOfTemperature.CELSIUS, precision=0,
            category=EntityCategory.DIAGNOSTIC,
        ),
        SensorSpec(
            key="battery_voltage", name="Battery Voltage", value_fn=get_battery_voltage,
            sensor_type=SensorType.DIAGNOSTIC, icon="mdi:battery",
            device_class=SensorDeviceClass.VOLTAGE,
            state_class=SensorStateClass.MEASUREMENT,
            unit=UnitOfElectricPotential.VOLT, precision=2,
            category=EntityCategory.DIAGNOSTIC,
        ),
    ]

    # Special sensors
    special_specs = [
        SensorSpec(
            key="session_time", name="Session Time", value_fn=get_session_time,
            sensor_type=SensorType.STATE, icon="mdi:timer",
            attributes_fn=get_session_time_attrs,
        ),
        SensorSpec(
            key="counter_a_cost", name="Counter A Cost", value_fn=get_counter_a_cost,
            sensor_type=SensorType.ENERGY, icon="mdi:currency-uah",
            state_class=SensorStateClass.TOTAL_INCREASING, unit="₴", precision=2,
        ),
        SensorSpec(
            key="counter_b_cost", name="Counter B Cost", value_fn=get_counter_b_cost,
            sensor_type=SensorType.ENERGY, icon="mdi:currency-uah",
            state_class=SensorStateClass.TOTAL_INCREASING, unit="₴", precision=2,
        ),
        SensorSpec(
            key="primary_rate_cost", name="Primary Rate Cost", value_fn=get_primary_rate_cost,
            sensor_type=SensorType.STATE, icon="mdi:currency-uah",
            state_class=SensorStateClass.MEASUREMENT, unit="₴/kWh", precision=2,
        ),
        SensorSpec(
            key="active_rate_cost", name="Active Rate Cost", value_fn=get_active_rate_cost,
            sensor_type=SensorType.STATE, icon="mdi:currency-uah",
            state_class=SensorStateClass.MEASUREMENT, unit="₴/kWh", precision=2,
            attributes_fn=get_active_rate_attrs,
        ),
        SensorSpec(
            key="rate_2_cost", name="Rate 2 Cost", value_fn=get_rate2_cost,
            sensor_type=SensorType.STATE, icon="mdi:currency-uah",
            state_class=SensorStateClass.MEASUREMENT, unit="₴/kWh", precision=2,
        ),
        SensorSpec(
            key="rate_3_cost", name="Rate 3 Cost", value_fn=get_rate3_cost,
            sensor_type=SensorType.STATE, icon="mdi:currency-uah",
            state_class=SensorStateClass.MEASUREMENT, unit="₴/kWh", precision=2,
        ),
        SensorSpec(
            key="rate_2_status", name="Rate 2 Status",
            value_fn=_make_rate_status_getter("tarifAEnable"),
            sensor_type=SensorType.STATE, icon="mdi:clock-check",
            category=EntityCategory.DIAGNOSTIC,
        ),
        SensorSpec(
            key="rate_3_status", name="Rate 3 Status",
            value_fn=_make_rate_status_getter("tarifBEnable"),
            sensor_type=SensorType.STATE, icon="mdi:clock-check",
            category=EntityCategory.DIAGNOSTIC,
        ),
        SensorSpec(
            key="connection_quality", name="Connection Quality",
            value_fn=get_connection_quality,
            sensor_type=SensorType.DIAGNOSTIC, icon="mdi:connection",
            state_class=SensorStateClass.MEASUREMENT, unit="%", precision=0,
            category=EntityCategory.DIAGNOSTIC, attributes_fn=get_connection_attrs,
        ),
    ]

    return measurement_specs + energy_specs + diagnostic_specs + special_specs


def get_sensor_specifications() -> List[SensorSpec]:
    """Get all sensor specifications."""
    return create_sensor_specifications()
