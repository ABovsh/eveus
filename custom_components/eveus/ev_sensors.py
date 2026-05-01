"""EV-specific sensors with optional helper support."""
from __future__ import annotations

import logging
import math
import time
from typing import Any, ClassVar, Optional, Dict, Set
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant, callback, Event
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.entity import EntityCategory

from .common import EveusSensorBase
from .utils import (
    calculate_remaining_time,
    calculate_soc_kwh_cached,
    calculate_soc_percent_cached,
    get_safe_value,
)
from .const import STATE_CACHE_TTL

_LOGGER = logging.getLogger(__name__)


# =============================================================================
# Input entity names
# =============================================================================

_INPUT_INITIAL_SOC = "input_number.ev_initial_soc"
_INPUT_BATTERY_CAPACITY = "input_number.ev_battery_capacity"
_INPUT_SOC_CORRECTION = "input_number.ev_soc_correction"
_INPUT_TARGET_SOC = "input_number.ev_target_soc"

_ALL_INPUTS = [_INPUT_INITIAL_SOC, _INPUT_BATTERY_CAPACITY, _INPUT_SOC_CORRECTION, _INPUT_TARGET_SOC]

_INPUT_LIMITS = {
    "initial_soc": (0, 100),
    "battery_capacity": (10, 160),
    "soc_correction": (0, 15),
    "target_soc": (0, 100),
}


# =============================================================================
# Shared SOC calculator
# =============================================================================

@dataclass
class InputEntityCache:
    """Cache for input entity values."""
    initial_soc: Optional[float] = None
    battery_capacity: Optional[float] = None
    soc_correction: Optional[float] = None
    target_soc: Optional[float] = None
    timestamp: float = 0
    helpers_available: bool = False

    def is_valid(self, ttl: float = STATE_CACHE_TTL) -> bool:
        """Check if cache is still valid."""
        return time.time() - self.timestamp < ttl


class CachedSOCCalculator:
    """SOC calculator with optional helper support."""

    def __init__(self, cache_ttl: int = STATE_CACHE_TTL):
        """Initialize with cache TTL."""
        self.cache_ttl = cache_ttl
        self._input_cache = InputEntityCache()

    def _mark_helpers_unavailable(self) -> None:
        """Cache helper unavailability and clear stale helper values."""
        self._input_cache = InputEntityCache(
            timestamp=time.time(),
            helpers_available=False,
        )

    def _update_input_cache(self, hass: HomeAssistant) -> bool:
        """Update input entity cache. Returns False if helpers not available."""
        if self._input_cache.is_valid(self.cache_ttl):
            return self._input_cache.helpers_available

        try:
            entities = {
                "initial_soc": hass.states.get(_INPUT_INITIAL_SOC),
                "battery_capacity": hass.states.get(_INPUT_BATTERY_CAPACITY),
                "soc_correction": hass.states.get(_INPUT_SOC_CORRECTION),
                "target_soc": hass.states.get(_INPUT_TARGET_SOC),
            }

            missing = [k for k, v in entities.items() if v is None]
            if missing:
                _LOGGER.debug("Optional EV helper entities not found: %s", missing)
                self._mark_helpers_unavailable()
                return False

            if not self._input_cache.helpers_available:
                _LOGGER.debug("All EV helper entities found. Advanced SOC metrics are available.")

            values: Dict[str, Any] = {"helpers_available": True}
            for key, entity in entities.items():
                try:
                    value = float(entity.state)
                except (ValueError, TypeError):
                    self._mark_helpers_unavailable()
                    return False

                minimum, maximum = _INPUT_LIMITS[key]
                if not math.isfinite(value) or value < minimum or value > maximum:
                    self._mark_helpers_unavailable()
                    return False

                values[key] = value

            for key, value in values.items():
                setattr(self._input_cache, key, value)
            self._input_cache.timestamp = time.time()
            return True

        except Exception as err:
            _LOGGER.debug("Error updating input cache: %s", err, exc_info=True)
            self._mark_helpers_unavailable()
            return False

    def get_soc_kwh(self, hass: HomeAssistant, energy_charged: float) -> Optional[float]:
        """Get SOC in kWh. Returns None if helpers not available."""
        if not self._update_input_cache(hass):
            return None
        try:
            return calculate_soc_kwh_cached(
                self._input_cache.initial_soc,
                self._input_cache.battery_capacity,
                energy_charged,
                self._input_cache.soc_correction or 7.5,
            )
        except Exception as err:
            _LOGGER.debug("Error calculating SOC kWh: %s", err, exc_info=True)
            return None

    def get_soc_percent(self, hass: HomeAssistant, energy_charged: float) -> Optional[float]:
        """Get SOC percentage. Returns None if helpers not available."""
        if not self._update_input_cache(hass):
            return None
        if not self._input_cache.battery_capacity:
            return None
        return calculate_soc_percent_cached(
            self._input_cache.initial_soc,
            self._input_cache.battery_capacity,
            energy_charged,
            self._input_cache.soc_correction or 7.5,
        )

    def invalidate_cache(self):
        """Force cache invalidation."""
        self._input_cache.timestamp = 0

    def are_helpers_available(self, hass: HomeAssistant) -> bool:
        """Check if helpers are available."""
        self._update_input_cache(hass)
        return self._input_cache.helpers_available

    @property
    def battery_capacity(self) -> Optional[float]:
        """Return cached battery capacity."""
        return self._input_cache.battery_capacity

    @property
    def soc_correction(self) -> float:
        """Return cached SOC correction or the integration default."""
        return self._input_cache.soc_correction or 7.5

    @property
    def target_soc(self) -> Optional[float]:
        """Return cached target SOC."""
        return self._input_cache.target_soc


# =============================================================================
# Common base for EV helper-dependent sensors
# =============================================================================

class BaseEVHelperSensor(EveusSensorBase):
    """Base class for sensors that depend on input_number helpers."""

    _tracked_inputs: tuple[str, ...] = ()
    _requires_helpers: ClassVar[bool] = True

    def __init__(
        self,
        updater,
        device_number: int = 1,
        soc_calculator: CachedSOCCalculator | None = None,
    ) -> None:
        """Initialize EV helper sensor."""
        super().__init__(updater, device_number)
        self._soc_calculator = soc_calculator or CachedSOCCalculator()
        self._last_update_time = 0
        self._cached_value = None
        self._helpers_available = False

    def _refresh_helpers_available(self) -> bool:
        """Refresh optional helper availability and return whether it changed."""
        previous = self._helpers_available
        self._helpers_available = self._soc_calculator.are_helpers_available(self.hass)
        return previous != self._helpers_available

    async def async_added_to_hass(self) -> None:
        """Set up state tracking for helper entities."""
        await super().async_added_to_hass()
        self._refresh_helpers_available()

        if self._tracked_inputs:
            try:
                self.async_on_remove(
                    async_track_state_change_event(
                        self.hass,
                        self._tracked_inputs,
                        self._on_input_changed,
                    )
                )
            except Exception as err:
                _LOGGER.debug(
                    "Could not set up state tracking for %s: %s",
                    self.unique_id,
                    err,
                    exc_info=True,
                )

    @callback
    def _on_input_changed(self, event: Event) -> None:
        """Handle input changes with rate limiting."""
        self._soc_calculator.invalidate_cache()
        previous_available = self.available
        helpers_changed = self._refresh_helpers_available()
        value_changed = self._update_native_value()
        attributes_changed = self._update_extra_state_attributes()

        current_time = time.time()
        if (
            helpers_changed
            or previous_available != self.available
            or value_changed
            or attributes_changed
            or current_time - self._last_update_time > 1
        ):
            self._last_update_time = current_time
            self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Available only when device is online AND helpers are present."""
        return super().available and (not self._requires_helpers or self._helpers_available)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle fresh coordinator data and optional helper availability."""
        previous_available = self.available
        self._refresh_helpers_available()
        availability_changed = self._update_availability_state()
        value_changed = self._update_native_value()
        attributes_changed = self._update_extra_state_attributes()
        if (
            availability_changed
            or previous_available != self.available
            or value_changed
            or attributes_changed
        ):
            self.async_write_ha_state()

    def _get_energy_charged(self) -> float:
        """Get energy charged from updater data with fallback."""
        energy_charged = get_safe_value(self._updater.data, "IEM1", float)
        if energy_charged is not None:
            return energy_charged
        return self.get_cached_data_value("IEM1", 0)


# =============================================================================
# Concrete EV sensors
# =============================================================================

class EVSocKwhSensor(BaseEVHelperSensor):
    """SOC energy sensor."""

    ENTITY_NAME = "SOC Energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:battery-charging"
    _attr_suggested_display_precision = 1
    _attr_state_class = SensorStateClass.TOTAL

    _tracked_inputs = (_INPUT_INITIAL_SOC, _INPUT_BATTERY_CAPACITY, _INPUT_SOC_CORRECTION)

    def _get_sensor_value(self) -> Optional[float]:
        """Get SOC in kWh."""
        if not self._soc_calculator.are_helpers_available(self.hass):
            return None
        result = self._soc_calculator.get_soc_kwh(self.hass, self._get_energy_charged())
        if result is not None:
            self._cached_value = result
        return result if result is not None else self._cached_value


class EVSocPercentSensor(BaseEVHelperSensor):
    """SOC percentage sensor."""

    ENTITY_NAME = "SOC Percent"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:battery-charging"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    _tracked_inputs = (_INPUT_INITIAL_SOC, _INPUT_BATTERY_CAPACITY, _INPUT_SOC_CORRECTION)

    def _get_sensor_value(self) -> Optional[float]:
        """Get SOC percentage."""
        if not self._soc_calculator.are_helpers_available(self.hass):
            return None
        result = self._soc_calculator.get_soc_percent(self.hass, self._get_energy_charged())
        if result is not None:
            self._cached_value = result
        return result if result is not None else self._cached_value


class TimeToTargetSocSensor(BaseEVHelperSensor):
    """Time to target SOC sensor."""

    ENTITY_NAME = "Time to Target SOC"
    _attr_icon = "mdi:timer"
    _requires_helpers = False

    _tracked_inputs = (
        _INPUT_INITIAL_SOC,
        _INPUT_TARGET_SOC,
        _INPUT_BATTERY_CAPACITY,
        _INPUT_SOC_CORRECTION,
    )

    def __init__(
        self,
        updater,
        device_number: int = 1,
        soc_calculator: CachedSOCCalculator | None = None,
    ) -> None:
        """Initialize with default cached value."""
        super().__init__(updater, device_number, soc_calculator)
        self._cached_value = "Helpers Required"

    def _get_sensor_value(self) -> str:
        """Calculate time to target."""
        if not self._soc_calculator.are_helpers_available(self.hass):
            return "Helpers Required"

        try:
            power_meas = get_safe_value(self._updater.data, "powerMeas", float)
            if power_meas is None:
                power_meas = self.get_cached_data_value("powerMeas", 0)
            energy_charged = self._get_energy_charged()

            if not self._soc_calculator.are_helpers_available(self.hass):
                return "Helpers Required"

            battery_capacity = self._soc_calculator.battery_capacity
            target_soc = self._soc_calculator.target_soc
            soc_correction = self._soc_calculator.soc_correction
            current_soc = self._soc_calculator.get_soc_percent(self.hass, energy_charged)

            if None in (battery_capacity, target_soc, current_soc):
                return "Helpers Required"

            result = calculate_remaining_time(
                current_soc=current_soc,
                target_soc=target_soc,
                power_meas=power_meas,
                battery_capacity=battery_capacity,
                correction=soc_correction,
            )
            self._cached_value = result
            return result

        except Exception as err:
            _LOGGER.debug(
                "Error calculating time to target for %s: %s",
                self.unique_id,
                err,
                exc_info=True,
            )
            return self._cached_value


# =============================================================================
# Input entity status sensor
# =============================================================================

class InputEntitiesStatusSensor(EveusSensorBase):
    """Sensor that monitors the status of optional input entities."""

    ENTITY_NAME = "Input Entities Status"
    _attr_icon = "mdi:clipboard-check"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    REQUIRED_INPUTS = {
        _INPUT_BATTERY_CAPACITY: {
            "name": "EV Battery Capacity",
            "min": 10, "max": 160, "step": 1, "initial": 80,
            "unit_of_measurement": "kWh", "mode": "slider",
            "icon": "mdi:car-battery",
        },
        _INPUT_INITIAL_SOC: {
            "name": "Initial EV State of Charge",
            "min": 0, "max": 100, "step": 1, "initial": 20,
            "unit_of_measurement": "%", "mode": "slider",
            "icon": "mdi:battery-charging-40",
        },
        _INPUT_SOC_CORRECTION: {
            "name": "Charging Efficiency Loss",
            "min": 0, "max": 15, "step": 0.1, "initial": 7.5,
            "unit_of_measurement": "%", "mode": "slider",
            "icon": "mdi:chart-bell-curve",
        },
        _INPUT_TARGET_SOC: {
            "name": "Target SOC",
            "min": 0, "max": 100, "step": 5, "initial": 80,
            "unit_of_measurement": "%", "mode": "slider",
            "icon": "mdi:battery-charging-high",
        },
    }

    def __init__(self, updater, device_number: int = 1) -> None:
        """Initialize input status sensor."""
        super().__init__(updater, device_number)
        self._state = "Unknown"
        self._missing_entities: Set[str] = set()
        self._invalid_entities: Set[str] = set()
        self._last_check_time = 0
        self._check_interval = STATE_CACHE_TTL
        self._configuration_help = self._build_configuration_help()
        self._attr_extra_state_attributes = {}

    def _build_configuration_help(self) -> Dict[str, str]:
        """Build static helper creation hints once."""
        help_text = {}
        for entity_id, config in self.REQUIRED_INPUTS.items():
            input_name = entity_id.split(".", 1)[1]
            help_text[entity_id] = (
                f"{input_name}:\n"
                f"  name: '{config['name']}'\n"
                f"  min: {config['min']}\n"
                f"  max: {config['max']}\n"
                f"  step: {config['step']}\n"
                f"  initial: {config['initial']}\n"
                f"  unit_of_measurement: '{config['unit_of_measurement']}'\n"
                f"  mode: {config['mode']}\n"
                f"  icon: '{config['icon']}'"
            )
        return help_text

    def _get_sensor_value(self) -> str:
        """Get input status with caching."""
        current_time = time.time()
        if current_time - self._last_check_time > self._check_interval:
            self._check_inputs()
            self._last_check_time = current_time
        return self._state

    def _build_extra_state_attributes(self) -> Dict[str, Any]:
        """Build cached status attributes from the latest input check."""
        attrs = {
            "missing_entities": list(self._missing_entities),
            "invalid_entities": list(self._invalid_entities),
            "required_count": len(self.REQUIRED_INPUTS),
            "missing_count": len(self._missing_entities),
            "invalid_count": len(self._invalid_entities),
            "status_summary": {
                eid: ("Missing" if eid in self._missing_entities
                      else "Invalid" if eid in self._invalid_entities
                      else "OK")
                for eid in self.REQUIRED_INPUTS
            },
            "note": "These helpers are optional. Advanced SOC metrics require them.",
        }

        if self._missing_entities:
            attrs["configuration_help"] = {
                entity_id: self._configuration_help[entity_id]
                for entity_id in self._missing_entities
                if entity_id in self._configuration_help
            }

        return attrs

    def _update_extra_state_attributes(self) -> bool:
        """Refresh cached status attributes."""
        try:
            previous_attrs = self._attr_extra_state_attributes
            self._attr_extra_state_attributes = self._build_extra_state_attributes()
            return previous_attrs != self._attr_extra_state_attributes
        except Exception as err:
            _LOGGER.debug(
                "Error getting attributes for %s: %s",
                self.unique_id,
                err,
                exc_info=True,
            )
            self._attr_extra_state_attributes = {}
            return True

    def _check_inputs(self) -> None:
        """Check all required inputs."""
        try:
            self._missing_entities.clear()
            self._invalid_entities.clear()

            for entity_id in self.REQUIRED_INPUTS:
                state = self.hass.states.get(entity_id)
                if state is None:
                    self._missing_entities.add(entity_id)
                    continue
                try:
                    value = float(state.state)
                    config = self.REQUIRED_INPUTS[entity_id]
                    if (
                        not math.isfinite(value)
                        or value < config["min"]
                        or value > config["max"]
                    ):
                        self._invalid_entities.add(entity_id)
                except (ValueError, TypeError):
                    self._invalid_entities.add(entity_id)

            if self._missing_entities:
                self._state = f"Optional - {len(self._missing_entities)} Missing"
            elif self._invalid_entities:
                self._state = f"Invalid {len(self._invalid_entities)} Inputs"
            else:
                self._state = "All Present"
            self._update_extra_state_attributes()
        except Exception as err:
            _LOGGER.debug(
                "Error checking inputs for %s: %s",
                self.unique_id,
                err,
                exc_info=True,
            )
            self._state = "Error"
            self._update_extra_state_attributes()
