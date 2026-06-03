"""Sensor definitions and factory for Eveus integration."""
from __future__ import annotations

import logging
import math
from typing import Any, Callable, Dict, Final, Optional
from datetime import datetime, timezone
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.core import callback
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.helpers.entity import EntityCategory
from homeassistant.util import dt as dt_util

from .common_base import EveusSensorBase
from .const import (
    get_charging_state,
    get_error_state,
    get_normal_substate,
    CHARGING_STATES,
    RATE_STATES,
    ERROR_LOG_RATE_LIMIT,
    MIN_CURRENT,
    MODEL_MAX_CURRENT,
)
from .utils import RateLog, get_safe_value, format_duration

_LOGGER = logging.getLogger(__name__)
_MAX_ERROR_LOG_KEYS = 64
_SENSOR_FUNCTION_LOG = RateLog(max_keys=_MAX_ERROR_LOG_KEYS)
ICON_FLASH = "mdi:flash"
ICON_CURRENT_AC = "mdi:current-ac"
ICON_CURRENCY_UAH = "mdi:currency-uah"
UNIT_UAH_PER_KWH = "₴/kWh"
UNIT_UAH = "UAH"
_MAX_MODEL_CURRENT = max(MODEL_MAX_CURRENT.values())
# Upper sanity bound for the charger clock (epoch seconds at year 2100).
_MAX_SYSTEM_TIME = 4102444800
# Upper sanity ceilings for live telemetry. Real readings sit far below these;
# the bounds exist only to reject corrupt payload outliers (e.g. powerMeas
# 999999) before they reach HA long-term statistics. Generous on purpose.
_MAX_VOLTAGE = 500
_MAX_CURRENT = 200
_MAX_POWER = 100_000
# Largest plausible per-slot schedule energy cap (kWh).
_MAX_SCHEDULE_KWH = 200
_RATE_COST_KEYS: Final = {0: "tarif", 1: "tarifAValue", 2: "tarifBValue"}


def _should_log_error(function_name: str) -> bool:
    """Check if a module-level sensor helper should log an error."""
    return _SENSOR_FUNCTION_LOG.should_log(ERROR_LOG_RATE_LIMIT, function_name)


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
    state_class: Optional[SensorStateClass | str] = None
    unit: Optional[str] = None
    precision: Optional[int] = None
    category: Optional[EntityCategory] = None
    attributes_fn: Optional[Callable] = None
    tracks_reset: bool = False

    def create_sensor(self, updater, device_number: int = 1) -> "OptimizedEveusSensor":
        """Create sensor instance from specification."""
        cls = MonetaryCostSensor if self.tracks_reset else OptimizedEveusSensor
        return cls(updater, self, device_number)


class OptimizedEveusSensor(EveusSensorBase):
    """High-performance templated sensor."""

    def __init__(self, updater, spec: SensorSpec, device_number: int = 1):
        """Initialize sensor from spec."""
        self.ENTITY_NAME = spec.name
        super().__init__(updater, device_number)

        self._spec = spec
        self._error_log = RateLog(max_keys=_MAX_ERROR_LOG_KEYS)

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
        self._attr_extra_state_attributes = {}

    def _should_log_error(self, function_name: str) -> bool:
        """Check if we should log errors for a function (rate limited)."""
        return self._error_log.should_log(ERROR_LOG_RATE_LIMIT, function_name)

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

    def _update_extra_state_attributes(self) -> bool:
        """Refresh cached attributes from coordinator data."""
        if not self._spec.attributes_fn:
            return False
        previous_attrs = self._attr_extra_state_attributes
        attrs: Dict[str, Any] = {}
        try:
            if self._updater.available:
                attrs = self._spec.attributes_fn(self._updater, self.hass)
        except Exception as err:
            if self._should_log_error(f"attributes_{self._spec.key}"):
                _LOGGER.debug(
                    "Error getting attributes for %s: %s",
                    self.name,
                    err,
                    exc_info=True,
                )
        self._attr_extra_state_attributes = attrs or {}
        return previous_attrs != self._attr_extra_state_attributes


class MonetaryCostSensor(OptimizedEveusSensor):
    """Cost sensor that tracks meter resets so TOTAL statistics stay correct.

    Monetary sensors must use ``state_class=TOTAL`` (Home Assistant forbids
    TOTAL_INCREASING for the monetary device class), but the charger resets
    ``sessionMoney`` every session and clears the IEM*_money counters on demand.
    Without a ``last_reset`` marker HA computes a negative delta on each reset
    and the long-term cost ``sum`` under-counts. We advance ``last_reset`` only
    when the value actually drops, which tells the recorder to start a fresh
    accumulation window instead of subtracting the pre-reset total.
    """

    def __init__(self, updater, spec: "SensorSpec", device_number: int = 1) -> None:
        """Initialize the cost sensor with reset tracking state."""
        super().__init__(updater, spec, device_number)
        self._prev_cost_value: Optional[float] = None
        self._attr_last_reset = None

    def _update_native_value(self) -> bool:
        """Refresh value and advance last_reset when the meter resets."""
        changed = super()._update_native_value()
        value = self._attr_native_value
        # Offline/None: leave the accumulation window untouched so an outage is
        # never mistaken for a meter reset.
        if value is not None:
            # First reading opens the accumulation window; a drop in cumulative
            # cost can only mean a charger-side reset (new session or manual
            # counter clear), since cost can only rise within a window.
            window_restart = self._attr_last_reset is None or (
                self._prev_cost_value is not None and value < self._prev_cost_value
            )
            if window_restart:
                self._attr_last_reset = dt_util.utcnow()
            self._prev_cost_value = value
        return changed

    async def _async_restore_state(self, state) -> None:
        """Restore the previous accumulation window across restarts."""
        await super()._async_restore_state(state)
        last_reset = state.attributes.get("last_reset")
        if last_reset is not None:
            parsed = (
                dt_util.parse_datetime(last_reset)
                if isinstance(last_reset, str)
                else last_reset
            )
            if parsed is not None:
                self._attr_last_reset = parsed
        try:
            self._prev_cost_value = float(state.state)
        except (TypeError, ValueError):
            self._prev_cost_value = None


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

def _make_value_getter(
    key: str,
    precision: int = 0,
    transform: Callable = None,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
):
    """Factory for simple data getter functions."""
    def getter(updater, hass):
        if not updater.available or not updater.data:
            return None
        raw = updater.data.get(key)
        if raw is None or isinstance(raw, bool):
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value):
            return None
        if minimum is not None and value < minimum:
            return None
        if maximum is not None and value > maximum:
            return None
        if transform:
            value = transform(value)
        return round(value, precision)
    return getter


def _make_enum_getter(key: str, mapping: dict[int, str]):
    """Read an int key and map it to a label, else None."""
    def getter(updater, hass) -> Optional[str]:
        value = _get_data_value(updater, key, int)
        return mapping.get(value)
    return getter


# Measurement getters
get_voltage = _make_value_getter("voltMeas1", precision=0, minimum=0, maximum=_MAX_VOLTAGE)
get_current = _make_value_getter("curMeas1", precision=1, minimum=0, maximum=_MAX_CURRENT)
get_power = _make_value_getter("powerMeas", precision=1, minimum=0, maximum=_MAX_POWER)
get_current_set = _make_value_getter(
    "currentSet", precision=0, minimum=MIN_CURRENT, maximum=_MAX_MODEL_CURRENT
)

# Energy getters
get_session_energy = _make_value_getter("sessionEnergy", precision=2, minimum=0)
get_total_energy = _make_value_getter("totalEnergy", precision=2, minimum=0)
get_counter_a_energy = _make_value_getter("IEM1", precision=2, minimum=0)
get_counter_b_energy = _make_value_getter("IEM2", precision=2, minimum=0)

# Cost getters.
# Firmware contract:
#   * `tarif*` fields are reported in HUNDREDTHS of a currency unit (kop/cent),
#     so they must be divided by 100 to get the per-kWh price.
#   * `IEM1_money`, `IEM2_money`, `sessionMoney` are already in WHOLE currency
#     units — DO NOT divide. Verified against R3.05.2 firmware.
_div100 = lambda v: v / 100
get_counter_a_cost = _make_value_getter("IEM1_money", precision=2, minimum=0)
get_counter_b_cost = _make_value_getter("IEM2_money", precision=2, minimum=0)
get_primary_rate_cost = _make_value_getter("tarif", precision=2, transform=_div100, minimum=0)
get_rate2_cost = _make_value_getter("tarifAValue", precision=2, transform=_div100, minimum=0)
get_rate3_cost = _make_value_getter("tarifBValue", precision=2, transform=_div100, minimum=0)

# Temperature getters
get_box_temperature = _make_value_getter("temperature1", precision=0)
get_plug_temperature = _make_value_getter("temperature2", precision=0)

# Other diagnostic getters
get_battery_voltage = _make_value_getter("vBat", precision=2, minimum=0)
get_leak_current = _make_value_getter("leakValue", precision=0, minimum=0)
get_leak_current_peak = _make_value_getter("leakValueH", precision=0, minimum=0)
# RSSI is reported in dBm — physically always ≤ 0 (typical floor ~ −120 dBm).
get_wifi_rssi = _make_value_getter("RSSI", precision=0, minimum=-120, maximum=0)

# 3-phase per-phase getters (only registered when entry is configured for 3 phases)
get_current_phase_2 = _make_value_getter("curMeas2", precision=1, minimum=0, maximum=_MAX_CURRENT)
get_current_phase_3 = _make_value_getter("curMeas3", precision=1, minimum=0, maximum=_MAX_CURRENT)
get_voltage_phase_2 = _make_value_getter("voltMeas2", precision=0, minimum=0, maximum=_MAX_VOLTAGE)
get_voltage_phase_3 = _make_value_getter("voltMeas3", precision=0, minimum=0, maximum=_MAX_VOLTAGE)


# =============================================================================
# State-based getters (need custom logic)
# =============================================================================

def get_charger_state(updater, hass) -> Optional[str]:
    """Get charger state."""
    state_value = _get_data_value(updater, "state", int)
    return get_charging_state(state_value) if state_value is not None else None


def get_charger_substate(updater, hass) -> Optional[str]:
    """Get charger substate.

    Returns None when the device state itself is outside the known domain —
    otherwise a stray firmware state would be labelled with normal-mode substate
    text and look like a plausible diagnostic reason.
    """
    state = _get_data_value(updater, "state", int)
    substate = _get_data_value(updater, "subState", int)
    if None in (state, substate):
        return None
    if state not in CHARGING_STATES:
        return None
    if state == 7:
        return get_error_state(substate)
    return get_normal_substate(substate)


get_ground_status = _make_enum_getter("ground", {1: "Connected", 0: "Not Connected"})


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

        # Reject obviously corrupt clock values (negative or far-future) so a
        # bad RTC reading is reported as unknown instead of a plausible time.
        if timestamp < 0 or timestamp > _MAX_SYSTEM_TIME:
            return None

        # The charger reports systemTime as a local wall-clock value encoded in
        # epoch seconds. Applying HA's timezone here shifts the displayed clock
        # by the local UTC offset, so format the encoded clock directly.
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%H:%M")

    except Exception as err:
        if _should_log_error("get_system_time"):
            _LOGGER.debug("Error getting system time: %s", err, exc_info=True)
        return None


def get_active_rate_cost(updater, hass) -> Optional[float]:
    """Get active rate cost."""
    active_rate = _get_data_value(updater, "activeTarif", int)
    if active_rate is None:
        return None
    key = _RATE_COST_KEYS.get(active_rate)
    if not key:
        return None
    value = _get_data_value(updater, key, float)
    if value is None or value < 0:
        return None
    return round(value / 100, 2)


def get_active_rate_attrs(updater, hass) -> dict:
    """Get active rate attributes."""
    if not updater.available:
        return {}
    active_rate = _get_data_value(updater, "activeTarif", int)
    return {"rate_name": RATE_STATES.get(active_rate, "Unknown")} if active_rate is not None else {}


def _make_rate_status_getter(rate_key: str):
    """Factory for rate status sensors."""
    return _make_enum_getter(rate_key, {1: "Enabled", 0: "Disabled"})


# =============================================================================
# Session cost — read directly from the charger's `sessionMoney` field.
#
# The charger itself integrates session cost using the rate active at the time
# of each energy delta, so there is no re-pricing on tariff change and no need
# for a stateful accumulator on the integration side.
# =============================================================================

get_session_cost = _make_value_getter("sessionMoney", precision=2, minimum=0)


# =============================================================================
# Adaptive charging (AI mode) and scheduled slots
# =============================================================================

get_adaptive_charging_state = _make_enum_getter("aiStatus", {1: "Active", 0: "Idle"})


get_adaptive_current = _make_value_getter(
    "aiModecurrent", precision=0, minimum=0, maximum=_MAX_CURRENT
)
get_adaptive_voltage = _make_value_getter(
    "aiVoltage", precision=0, minimum=0, maximum=_MAX_VOLTAGE
)


def _format_minutes(value: Optional[int]) -> Optional[str]:
    """Convert minutes-since-midnight to HH:MM."""
    if value is None or not 0 <= value < 1440:
        return None
    return f"{value // 60:02d}:{value % 60:02d}"


def _make_schedule_getter(slot: int):
    """Slot enabled/disabled state."""
    key = f"sh{slot}Enabled"
    return _make_enum_getter(key, {1: "Enabled", 0: "Disabled"})


def _make_schedule_attrs(slot: int):
    """Slot details: window, optional current/energy caps."""
    def getter(updater, hass) -> dict:
        if not updater.available:
            return {}
        start = _format_minutes(_get_data_value(updater, f"sh{slot}Start", int))
        stop = _format_minutes(_get_data_value(updater, f"sh{slot}Stop", int))
        attrs: Dict[str, Any] = {}
        if start and stop:
            attrs["window"] = f"{start}–{stop}"
            attrs["start"] = start
            attrs["stop"] = stop
        if _get_data_value(updater, f"sh{slot}CurrentEnable", int) == 1:
            cur = _get_data_value(updater, f"sh{slot}CurrentValue", int)
            if cur is not None and MIN_CURRENT <= cur <= _MAX_MODEL_CURRENT:
                attrs["current_limit_a"] = cur
        if _get_data_value(updater, f"sh{slot}EnergyEnable", int) == 1:
            energy = _get_data_value(updater, f"sh{slot}EnergyValue", float)
            if energy is not None and 0 <= energy <= _MAX_SCHEDULE_KWH:
                attrs["energy_limit_kwh"] = energy
        return attrs
    return getter


# =============================================================================
# Connection quality
# =============================================================================

def get_connection_quality(updater, hass) -> Optional[float]:
    """Get connection quality as numeric value.

    Returns None on exception so a calculation failure doesn't masquerade as
    100% (excellent) — the sensor goes unknown, which is the correct signal.
    """
    try:
        metrics = updater.connection_quality
        rate = metrics.get("success_rate", 0)
        if not isinstance(rate, (int, float)) or isinstance(rate, bool) or not math.isfinite(rate):
            return None
        return round(max(0, min(100, rate)))
    except Exception as err:
        if _should_log_error("get_connection_quality"):
            _LOGGER.debug("Error getting connection quality: %s", err, exc_info=True)
        return None


def get_connection_attrs(updater, hass) -> dict:
    """Get connection attributes.

    Connection Quality measures HA→charger HTTP poll success.
    `wifi_rssi` is included as a supplementary metric (charger→AP link)
    because a degraded RSSI is the most common upstream cause of poor
    Connection Quality — surfacing both in one view makes diagnosis faster.
    """
    try:
        if not updater.available:
            return {}
        metrics = updater.connection_quality
        success_rate = metrics.get("success_rate", 100)
        latency_avg = max(0.0, metrics.get("latency_avg", 0.0))
        if success_rate > 95:
            status = "Excellent"
        elif success_rate > 80:
            status = "Good"
        elif success_rate > 60:
            status = "Fair"
        elif success_rate > 30:
            status = "Poor"
        else:
            status = "Critical"
        attrs: dict[str, Any] = {
            "connection_quality": round(success_rate),
            "latency_avg": round(latency_avg * 2) / 2,
            "status": status,
        }
        rssi = get_wifi_rssi(updater, hass)
        if rssi is not None:
            attrs["wifi_rssi"] = rssi
        return attrs
    except Exception as err:
        if _should_log_error("get_connection_attrs"):
            _LOGGER.debug("Error getting connection attributes: %s", err, exc_info=True)
        return {"status": "Error"}


# =============================================================================
# Sensor specification factory
# =============================================================================

def create_sensor_specifications(
    phases: int = 1, max_current: int = _MAX_MODEL_CURRENT
) -> tuple[SensorSpec, ...]:
    """Create all sensor specifications using factory pattern.

    ``phases`` toggles per-phase voltage/current sensors for 3-phase chargers.
    ``max_current`` bounds the Current Set diagnostic sensor to the configured
    model's maximum, so a corrupt ``currentSet`` above the charger's capability
    (e.g. 48 A reported by a 16 A unit) reads as ``unknown`` instead of a
    plausible-but-impossible value.
    """

    # Bound Current Set to this charger's model maximum rather than the global
    # ceiling shared by all models.
    current_set_getter = _make_value_getter(
        "currentSet", precision=0, minimum=MIN_CURRENT, maximum=max_current
    )

    # Measurement sensors
    measurements = [
        ("Voltage", get_voltage, ICON_FLASH, SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT, 0, None),
        ("Current", get_current, ICON_CURRENT_AC, SensorDeviceClass.CURRENT, UnitOfElectricCurrent.AMPERE, 1, None),
        ("Power", get_power, ICON_FLASH, SensorDeviceClass.POWER, UnitOfPower.WATT, 1, None),
        (
            "Current Set",
            current_set_getter,
            ICON_CURRENT_AC,
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

    # Energy sensors.
    # Session Energy resets to 0 each session and is deliberately kept out of
    # the Energy Dashboard, so it is a plain MEASUREMENT with no device class
    # (HA forbids ENERGY + MEASUREMENT). The lifetime/counter meters increase
    # and reset, so they use the ENERGY device class with TOTAL_INCREASING.
    energy_sensors = [
        ("Session Energy", get_session_energy, "mdi:transmission-tower-export", SensorStateClass.MEASUREMENT, None),
        ("Total Energy", get_total_energy, "mdi:transmission-tower", SensorStateClass.TOTAL_INCREASING, SensorDeviceClass.ENERGY),
        ("Counter A Energy", get_counter_a_energy, "mdi:counter", SensorStateClass.TOTAL_INCREASING, SensorDeviceClass.ENERGY),
        ("Counter B Energy", get_counter_b_energy, "mdi:counter", SensorStateClass.TOTAL_INCREASING, SensorDeviceClass.ENERGY),
    ]

    energy_specs = [
        SensorSpec(
            key=name.lower().replace(" ", "_"),
            name=name,
            value_fn=fn,
            sensor_type=SensorType.ENERGY,
            icon=icon,
            device_class=device_class,
            state_class=state_class,
            unit=UnitOfEnergy.KILO_WATT_HOUR,
            precision=2,
        )
        for name, fn, icon, state_class, device_class in energy_sensors
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
        SensorSpec(
            key="leak_current", name="Leakage Current", value_fn=get_leak_current,
            sensor_type=SensorType.DIAGNOSTIC, icon="mdi:current-dc",
            device_class=SensorDeviceClass.CURRENT,
            state_class=SensorStateClass.MEASUREMENT,
            unit=UnitOfElectricCurrent.MILLIAMPERE, precision=0,
            category=EntityCategory.DIAGNOSTIC,
        ),
        SensorSpec(
            key="leak_current_peak", name="Leakage Current Peak",
            value_fn=get_leak_current_peak,
            sensor_type=SensorType.DIAGNOSTIC, icon="mdi:current-dc",
            device_class=SensorDeviceClass.CURRENT,
            state_class=SensorStateClass.MEASUREMENT,
            unit=UnitOfElectricCurrent.MILLIAMPERE, precision=0,
            category=EntityCategory.DIAGNOSTIC,
        ),
        SensorSpec(
            key="wifi_signal", name="WiFi Signal",
            value_fn=get_wifi_rssi,
            sensor_type=SensorType.DIAGNOSTIC, icon="mdi:wifi",
            device_class=SensorDeviceClass.SIGNAL_STRENGTH,
            state_class=SensorStateClass.MEASUREMENT,
            unit=SIGNAL_STRENGTH_DECIBELS_MILLIWATT, precision=0,
            category=EntityCategory.DIAGNOSTIC,
        ),
    ]

    if phases == 3:
        diagnostic_specs.extend([
            SensorSpec(
                key="current_phase_2", name="Current Phase 2",
                value_fn=get_current_phase_2,
                sensor_type=SensorType.MEASUREMENT, icon=ICON_CURRENT_AC,
                device_class=SensorDeviceClass.CURRENT,
                state_class=SensorStateClass.MEASUREMENT,
                unit=UnitOfElectricCurrent.AMPERE, precision=1,
            ),
            SensorSpec(
                key="current_phase_3", name="Current Phase 3",
                value_fn=get_current_phase_3,
                sensor_type=SensorType.MEASUREMENT, icon=ICON_CURRENT_AC,
                device_class=SensorDeviceClass.CURRENT,
                state_class=SensorStateClass.MEASUREMENT,
                unit=UnitOfElectricCurrent.AMPERE, precision=1,
            ),
            SensorSpec(
                key="voltage_phase_2", name="Voltage Phase 2",
                value_fn=get_voltage_phase_2,
                sensor_type=SensorType.MEASUREMENT, icon=ICON_FLASH,
                device_class=SensorDeviceClass.VOLTAGE,
                state_class=SensorStateClass.MEASUREMENT,
                unit=UnitOfElectricPotential.VOLT, precision=0,
            ),
            SensorSpec(
                key="voltage_phase_3", name="Voltage Phase 3",
                value_fn=get_voltage_phase_3,
                sensor_type=SensorType.MEASUREMENT, icon=ICON_FLASH,
                device_class=SensorDeviceClass.VOLTAGE,
                state_class=SensorStateClass.MEASUREMENT,
                unit=UnitOfElectricPotential.VOLT, precision=0,
            ),
        ])

    # Special sensors
    special_specs = [
        SensorSpec(
            key="session_time", name="Session Time", value_fn=get_session_time,
            sensor_type=SensorType.STATE, icon="mdi:timer",
            attributes_fn=get_session_time_attrs,
        ),
        SensorSpec(
            key="counter_a_cost", name="Counter A Cost", value_fn=get_counter_a_cost,
            sensor_type=SensorType.ENERGY, icon=ICON_CURRENCY_UAH,
            device_class=SensorDeviceClass.MONETARY,
            state_class=SensorStateClass.TOTAL, unit=UNIT_UAH, precision=2,
            tracks_reset=True,
        ),
        SensorSpec(
            key="counter_b_cost", name="Counter B Cost", value_fn=get_counter_b_cost,
            sensor_type=SensorType.ENERGY, icon=ICON_CURRENCY_UAH,
            device_class=SensorDeviceClass.MONETARY,
            state_class=SensorStateClass.TOTAL, unit=UNIT_UAH, precision=2,
            tracks_reset=True,
        ),
        SensorSpec(
            key="primary_rate_cost", name="Primary Rate Cost", value_fn=get_primary_rate_cost,
            sensor_type=SensorType.STATE, icon=ICON_CURRENCY_UAH,
            state_class=SensorStateClass.MEASUREMENT, unit=UNIT_UAH_PER_KWH, precision=2,
        ),
        SensorSpec(
            key="active_rate_cost", name="Active Rate Cost", value_fn=get_active_rate_cost,
            sensor_type=SensorType.STATE, icon=ICON_CURRENCY_UAH,
            state_class=SensorStateClass.MEASUREMENT, unit=UNIT_UAH_PER_KWH, precision=2,
            attributes_fn=get_active_rate_attrs,
        ),
        SensorSpec(
            key="rate_2_cost", name="Rate 2 Cost", value_fn=get_rate2_cost,
            sensor_type=SensorType.STATE, icon=ICON_CURRENCY_UAH,
            state_class=SensorStateClass.MEASUREMENT, unit=UNIT_UAH_PER_KWH, precision=2,
        ),
        SensorSpec(
            key="rate_3_cost", name="Rate 3 Cost", value_fn=get_rate3_cost,
            sensor_type=SensorType.STATE, icon=ICON_CURRENCY_UAH,
            state_class=SensorStateClass.MEASUREMENT, unit=UNIT_UAH_PER_KWH, precision=2,
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
            key="session_cost", name="Session Cost", value_fn=get_session_cost,
            sensor_type=SensorType.STATE, icon="mdi:cash",
            device_class=SensorDeviceClass.MONETARY,
            state_class=SensorStateClass.TOTAL, unit=UNIT_UAH, precision=2,
            tracks_reset=True,
        ),
        SensorSpec(
            key="adaptive_charging", name="Adaptive Charging",
            value_fn=get_adaptive_charging_state,
            sensor_type=SensorType.DIAGNOSTIC, icon="mdi:auto-mode",
            category=EntityCategory.DIAGNOSTIC,
        ),
        SensorSpec(
            key="adaptive_current_limit", name="Adaptive Current Limit",
            value_fn=get_adaptive_current,
            sensor_type=SensorType.DIAGNOSTIC, icon=ICON_CURRENT_AC,
            device_class=SensorDeviceClass.CURRENT,
            state_class=SensorStateClass.MEASUREMENT,
            unit=UnitOfElectricCurrent.AMPERE, precision=0,
            category=EntityCategory.DIAGNOSTIC,
        ),
        SensorSpec(
            key="adaptive_voltage_threshold", name="Adaptive Voltage Threshold",
            value_fn=get_adaptive_voltage,
            sensor_type=SensorType.DIAGNOSTIC, icon="mdi:flash-alert",
            device_class=SensorDeviceClass.VOLTAGE,
            state_class=SensorStateClass.MEASUREMENT,
            unit=UnitOfElectricPotential.VOLT, precision=0,
            category=EntityCategory.DIAGNOSTIC,
        ),
        SensorSpec(
            key="schedule_1", name="Schedule 1",
            value_fn=_make_schedule_getter(1),
            attributes_fn=_make_schedule_attrs(1),
            sensor_type=SensorType.DIAGNOSTIC, icon="mdi:calendar-clock",
            category=EntityCategory.DIAGNOSTIC,
        ),
        SensorSpec(
            key="schedule_2", name="Schedule 2",
            value_fn=_make_schedule_getter(2),
            attributes_fn=_make_schedule_attrs(2),
            sensor_type=SensorType.DIAGNOSTIC, icon="mdi:calendar-clock",
            category=EntityCategory.DIAGNOSTIC,
        ),
        SensorSpec(
            key="connection_quality", name="Connection Quality",
            value_fn=get_connection_quality,
            sensor_type=SensorType.DIAGNOSTIC, icon="mdi:connection",
            state_class=SensorStateClass.MEASUREMENT, unit=PERCENTAGE, precision=0,
            category=EntityCategory.DIAGNOSTIC, attributes_fn=get_connection_attrs,
        ),
    ]

    result = tuple(measurement_specs + energy_specs + diagnostic_specs + special_specs)
    keys = [s.key for s in result]
    if len(keys) != len(set(keys)):
        duplicates = sorted({k for k in keys if keys.count(k) > 1})
        raise RuntimeError(f"duplicate sensor keys: {duplicates}")
    return result


@lru_cache(maxsize=8)
def get_sensor_specifications(
    phases: int = 1, max_current: Optional[int] = None
) -> tuple[SensorSpec, ...]:
    """Get sensor specifications for the given phase count and model max (cached)."""
    return create_sensor_specifications(
        phases=phases, max_current=max_current or _MAX_MODEL_CURRENT
    )
