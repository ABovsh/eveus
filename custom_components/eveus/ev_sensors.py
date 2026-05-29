"""EV-specific sensors with optional helper support."""
from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timedelta
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
from homeassistant.util import dt as dt_util

from .common_base import EveusSensorBase
from .utils import (
    calculate_remaining_seconds,
    calculate_remaining_time,
    calculate_soc_kwh,
    calculate_soc_percent,
    get_safe_value,
)
from .const import DEFAULT_SOC_CORRECTION, STATE_CACHE_TTL

_LOGGER = logging.getLogger(__name__)


# =============================================================================
# Input entity names
# =============================================================================

_HELPERS_REQUIRED = "Helpers Required"

_INPUT_INITIAL_SOC = "input_number.ev_initial_soc"
_INPUT_BATTERY_CAPACITY = "input_number.ev_battery_capacity"
_INPUT_SOC_CORRECTION = "input_number.ev_soc_correction"
_INPUT_TARGET_SOC = "input_number.ev_target_soc"

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

    # SOC % / kWh only need these three helpers. Target SOC is consumed only
    # by ETA-class sensors (Time to Target SOC, Charging Finish Time), so we
    # don't gate SOC availability on it — otherwise a slow-loading
    # `input_number.ev_target_soc` at HA startup would mask SOC entirely.
    _SOC_REQUIRED_KEYS = ("initial_soc", "battery_capacity", "soc_correction")

    @staticmethod
    def _get_input_entities(hass: HomeAssistant) -> Dict[str, Any]:
        """Return SOC helper states keyed by cache field name."""
        return {
            "initial_soc": hass.states.get(_INPUT_INITIAL_SOC),
            "battery_capacity": hass.states.get(_INPUT_BATTERY_CAPACITY),
            "soc_correction": hass.states.get(_INPUT_SOC_CORRECTION),
            "target_soc": hass.states.get(_INPUT_TARGET_SOC),
        }

    def _is_required_key(self, key: str) -> bool:
        """Return True when a helper key is required for SOC sensors."""
        return key in self._SOC_REQUIRED_KEYS

    def _read_helper_value(self, key: str, entity: Any) -> Optional[float]:
        """Return a valid helper value, or None for invalid optional helpers."""
        try:
            value = float(entity.state)
        except (ValueError, TypeError):
            if self._is_required_key(key):
                raise
            return None

        minimum, maximum = _INPUT_LIMITS[key]
        if not math.isfinite(value) or value < minimum or value > maximum:
            if self._is_required_key(key):
                raise ValueError(f"{key} is outside valid range")
            return None
        return value

    def _update_input_cache(self, hass: HomeAssistant) -> bool:
        """Refresh helper cache. Returns True when SOC helpers are usable.

        Target SOC is treated as optional: when missing or invalid we still
        report helpers_available=True (so SOC sensors work) and leave
        `target_soc` as None for ETA sensors to detect.
        """
        if self._input_cache.is_valid(self.cache_ttl):
            return self._input_cache.helpers_available

        try:
            entities = self._get_input_entities(hass)

            missing_soc = [k for k in self._SOC_REQUIRED_KEYS if entities[k] is None]
            if missing_soc:
                _LOGGER.debug("Required SOC helper entities not found: %s", missing_soc)
                self._mark_helpers_unavailable()
                return False

            values: Dict[str, Any] = {"helpers_available": True}
            for key, entity in entities.items():
                if entity is None:
                    # Optional (target_soc); leave at None.
                    continue
                try:
                    value = self._read_helper_value(key, entity)
                except (ValueError, TypeError):
                    self._mark_helpers_unavailable()
                    return False
                if value is not None:
                    values[key] = value

            if not self._input_cache.helpers_available:
                _LOGGER.debug("SOC helper entities resolved (target_soc=%s).", values.get("target_soc"))

            # Reset previous-cycle values (esp. target_soc) before overwriting.
            self._input_cache.target_soc = None
            for key, value in values.items():
                setattr(self._input_cache, key, value)
            self._input_cache.timestamp = time.time()
            return True

        except Exception as err:
            _LOGGER.debug("Error updating input cache: %s", err, exc_info=True)
            self._mark_helpers_unavailable()
            return False

    def _effective_correction(self) -> float:
        """Cached SOC correction, preserving an explicit 0% configuration."""
        correction = self._input_cache.soc_correction
        return DEFAULT_SOC_CORRECTION if correction is None else correction

    def get_soc_kwh(self, hass: HomeAssistant, energy_charged: float) -> Optional[float]:
        """Get SOC in kWh. Returns None if helpers not available."""
        if not self._update_input_cache(hass):
            return None
        try:
            return calculate_soc_kwh(
                self._input_cache.initial_soc,
                self._input_cache.battery_capacity,
                energy_charged,
                self._effective_correction(),
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
        return calculate_soc_percent(
            self._input_cache.initial_soc,
            self._input_cache.battery_capacity,
            energy_charged,
            self._effective_correction(),
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
        """Return cached SOC correction, preserving an explicit 0% config."""
        return self._effective_correction()

    @property
    def target_soc(self) -> Optional[float]:
        """Return cached target SOC."""
        return self._input_cache.target_soc

    @property
    def initial_soc(self) -> Optional[float]:
        """Return cached initial SOC."""
        return self._input_cache.initial_soc


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
        self._maybe_finalize_device_info()
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

    def _resolve_remaining_inputs(self) -> tuple | None:
        """Collect the inputs needed to compute remaining-charge ETA.

        Returns a tuple (current_soc, target_soc, power_meas, battery_capacity,
        correction) when every input is present, otherwise None.
        """
        if not self._soc_calculator.are_helpers_available(self.hass):
            return None
        power_meas = get_safe_value(self._updater.data, "powerMeas", float)
        energy_charged = self._get_energy_charged()
        if power_meas is None or energy_charged is None:
            return None
        battery_capacity = self._soc_calculator.battery_capacity
        target_soc = self._soc_calculator.target_soc
        soc_correction = self._soc_calculator.soc_correction
        current_soc = self._soc_calculator.get_soc_percent(self.hass, energy_charged)
        if None in (battery_capacity, target_soc, current_soc):
            return None
        return (current_soc, target_soc, power_meas, battery_capacity, soc_correction)

    def _get_energy_charged(self) -> float | None:
        """Energy delivered in the current session, in kWh.

        Uses the charger's native ``sessionEnergy`` field, which the charger
        itself resets to 0 on each new session (plug-in). This avoids the
        cross-restart baseline persistence machinery the integration carried
        through 4.5.x: there is nothing to snapshot, restore, or invalidate.

        Trade-off: split charging across plug-in/out cycles requires the user
        to update ``input_number.ev_initial_soc`` before unplugging, since the
        charger starts a fresh session count on the next plug-in.
        """
        value = get_safe_value(self._updater.data, "sessionEnergy", float)
        if value is None or value < 0:
            return None
        return value

    def _session_energy_is_invalid(self) -> bool:
        """True when sessionEnergy is reported but not a usable value.

        Distinguishes a present-but-corrupt reading (e.g. negative) from the
        field simply not being reported yet, so callers don't silently treat a
        bad reading as 0 kWh delivered (which would mimic the initial SOC).
        """
        data = self._updater.data or {}
        return "sessionEnergy" in data and self._get_energy_charged() is None


# =============================================================================
# Concrete EV sensors
# =============================================================================

class EVSocKwhSensor(BaseEVHelperSensor):
    """SOC energy sensor — battery energy in kWh from session delivered."""

    ENTITY_NAME = "SOC Energy"
    # Stored energy currently in the battery — a level, not a cumulative meter —
    # so ENERGY_STORAGE (the device class HA pairs with MEASUREMENT) rather than
    # ENERGY (which HA only allows with TOTAL/TOTAL_INCREASING).
    _attr_device_class = SensorDeviceClass.ENERGY_STORAGE
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:battery-charging"
    _attr_suggested_display_precision = 1
    _attr_state_class = SensorStateClass.MEASUREMENT

    _tracked_inputs = (_INPUT_INITIAL_SOC, _INPUT_BATTERY_CAPACITY, _INPUT_SOC_CORRECTION)

    def _get_sensor_value(self) -> Optional[float]:
        # If the charger has not yet reported sessionEnergy (cold start, offline
        # blip, or no session ever began), treat it as 0 delivered — SOC then
        # equals the user's Initial SOC. Prevents the entity from being
        # "unknown" the moment HA boots before the first successful poll.
        if self._session_energy_is_invalid():
            return None
        energy_charged = self._get_energy_charged() or 0.0
        result = self._soc_calculator.get_soc_kwh(self.hass, energy_charged)
        if result is not None:
            self._cached_value = result
        return self._cached_value


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
        # See EVSocKwhSensor._get_sensor_value — same Initial-SOC fallback.
        if self._session_energy_is_invalid():
            return None
        energy_charged = self._get_energy_charged() or 0.0
        result = self._soc_calculator.get_soc_percent(self.hass, energy_charged)
        if result is not None:
            self._cached_value = result
        return self._cached_value


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
        self._cached_value = _HELPERS_REQUIRED

    def _get_sensor_value(self) -> str:
        """Calculate time to target."""
        if not self._soc_calculator.are_helpers_available(self.hass):
            self._cached_value = _HELPERS_REQUIRED
            return self._cached_value

        if self._soc_calculator.target_soc is None:
            # The core SOC helpers are present but the optional Target SOC is
            # not — prompt for the one missing piece instead of the generic
            # "Helpers Required", which implies nothing is configured.
            self._cached_value = "Set Target SOC"
            return self._cached_value

        try:
            inputs = self._resolve_remaining_inputs()
            if inputs is None:
                # Helpers became unavailable mid-session — drop the stale ETA
                # rather than keeping the last "2h 15m" on screen forever.
                self._cached_value = _HELPERS_REQUIRED
                return self._cached_value
            result = calculate_remaining_time(*inputs)
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


class ChargingFinishTimeSensor(BaseEVHelperSensor):
    """Absolute timestamp when charging is expected to reach target SOC.

    Companion to `TimeToTargetSocSensor` — the latter is a UI string ("2h 15m"),
    this one is a `device_class=timestamp` value that automations and
    `device_class: timestamp` cards can consume directly (e.g. "notify me 30
    min before charge finishes"). Returns None when not charging, helpers
    missing, or target already reached.
    """

    ENTITY_NAME = "Charging Finish Time"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:calendar-clock"

    _tracked_inputs = (
        _INPUT_INITIAL_SOC,
        _INPUT_TARGET_SOC,
        _INPUT_BATTERY_CAPACITY,
        _INPUT_SOC_CORRECTION,
    )

    def _get_sensor_value(self) -> Optional[datetime]:
        """Compute the finish-time stamp."""
        try:
            inputs = self._resolve_remaining_inputs()
            if inputs is None:
                return None
            seconds = calculate_remaining_seconds(*inputs)
            if seconds is None or seconds <= 0:
                # None = not charging / invalid; 0 = target reached
                return None
            # Round to the next whole minute so the state doesn't jitter on
            # every poll (each tick would otherwise produce a fresh timestamp).
            eta = dt_util.utcnow() + timedelta(seconds=seconds)
            return eta.replace(second=0, microsecond=0) + timedelta(minutes=1)
        except Exception as err:
            _LOGGER.debug(
                "Error calculating finish time for %s: %s",
                self.unique_id,
                err,
                exc_info=True,
            )
            return None


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
        self._attr_extra_state_attributes = {}

    async def async_added_to_hass(self) -> None:
        """Subscribe to helper-entity state changes for instant updates."""
        await super().async_added_to_hass()
        try:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    tuple(self.REQUIRED_INPUTS),
                    self._on_input_state_changed,
                )
            )
        except Exception as err:
            _LOGGER.debug(
                "Could not set up input tracking for %s: %s",
                self.unique_id,
                err,
                exc_info=True,
            )

    @callback
    def _on_input_state_changed(self, _event: Event) -> None:
        """Re-check inputs immediately and push the new state to HA.

        SensorEntity has no async_update, so async_schedule_update_ha_state
        would write the cached value. Recompute value+attrs here, then write
        if anything changed.
        """
        self._last_check_time = 0
        value_changed = self._update_native_value()
        attrs_changed = self._update_extra_state_attributes()
        if value_changed or attrs_changed:
            self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Always available — reports local HA helper state, not charger data.

        Decoupled from charger availability so the diagnostic stays useful for
        troubleshooting missing SOC helpers while the charger is offline.
        """
        return True

    def _get_sensor_value(self) -> str:
        """Get input status with caching."""
        current_time = time.time()
        if current_time - self._last_check_time > self._check_interval:
            self._check_inputs()
            self._last_check_time = current_time
        return self._state

    def _build_extra_state_attributes(self) -> Dict[str, Any]:
        """Build cached status attributes from the latest input check.

        configuration_help is intentionally omitted: storing a multi-line YAML
        snippet per missing helper bloats every state_changed event and gets
        persisted by Recorder. README documents the helper format instead.
        """
        return {
            "missing_entities": sorted(self._missing_entities),
            "invalid_entities": sorted(self._invalid_entities),
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
        except Exception as err:
            _LOGGER.debug(
                "Error checking inputs for %s: %s",
                self.unique_id,
                err,
                exc_info=True,
            )
            self._state = "Error"
