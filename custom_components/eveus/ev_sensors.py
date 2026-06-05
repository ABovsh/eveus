"""EV-specific sensors with optional helper support."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import ClassVar, Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util

from .common_base import EveusSensorBase
from .utils import (
    calculate_remaining_seconds,
    calculate_remaining_time,
    calculate_soc_kwh,
    calculate_soc_percent,
    get_safe_value,
)
from .const import DEFAULT_SOC_CORRECTION, MAX_ENERGY_KWH, MAX_POWER_W, soc_update_signal

_LOGGER = logging.getLogger(__name__)


# =============================================================================
# Shared SOC calculator (pushed-value holder)
# =============================================================================

_SOC_REQUIRED_KEYS = ("initial_soc", "battery_capacity", "soc_correction")


class CachedSOCCalculator:
    """Holds SOC input values pushed from the native number entities."""

    def __init__(self) -> None:
        """Initialize with no values set."""
        self.initial_soc: Optional[float] = None
        self.battery_capacity: Optional[float] = None
        self.soc_correction_raw: Optional[float] = None
        self.target_soc: Optional[float] = None

    def set_value(self, key: str, value: Optional[float]) -> None:
        """Store a pushed SOC input value (None clears it)."""
        if key == "soc_correction":
            self.soc_correction_raw = value
        elif key in ("initial_soc", "battery_capacity", "target_soc"):
            setattr(self, key, value)

    def are_helpers_available(self) -> bool:
        """True when the three SOC-required values are present."""
        return (
            self.initial_soc is not None
            and self.battery_capacity is not None
            and self.soc_correction_raw is not None
        )

    def _effective_correction(self) -> float:
        """SOC correction, preserving an explicit 0% configuration."""
        return DEFAULT_SOC_CORRECTION if self.soc_correction_raw is None else self.soc_correction_raw

    @property
    def soc_correction(self) -> float:
        """Return effective SOC correction."""
        return self._effective_correction()

    def get_soc_kwh(self, energy_charged: float) -> Optional[float]:
        """Battery energy in kWh, or None when SOC inputs are missing."""
        if not self.are_helpers_available():
            return None
        try:
            return calculate_soc_kwh(
                self.initial_soc, self.battery_capacity, energy_charged, self._effective_correction()
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Error calculating SOC kWh: %s", err, exc_info=True)
            return None

    def get_soc_percent(self, energy_charged: float) -> Optional[float]:
        """Battery SOC percent, or None when SOC inputs are missing."""
        if not self.are_helpers_available() or not self.battery_capacity:
            return None
        return calculate_soc_percent(
            self.initial_soc, self.battery_capacity, energy_charged, self._effective_correction()
        )


# =============================================================================
# Common base for EV helper-dependent sensors
# =============================================================================

class BaseEVHelperSensor(EveusSensorBase):
    """Base class for SOC sensors fed by the native number entities."""

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
        self._cached_value = None

    async def async_added_to_hass(self) -> None:
        """Subscribe to per-entry SOC value updates."""
        await super().async_added_to_hass()
        entry_id = self._updater.config_entry.entry_id
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, soc_update_signal(entry_id), self._on_soc_input_changed
            )
        )

    @callback
    def _on_soc_input_changed(self) -> None:
        """Recompute immediately when a SOC input value is pushed."""
        previous_available = self.available
        value_changed = self._update_native_value()
        attributes_changed = self._update_extra_state_attributes()
        if value_changed or attributes_changed or previous_available != self.available:
            self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Available when online; SOC%/kWh additionally require inputs present."""
        if not super().available:
            return False
        if not self._requires_helpers:
            return True
        return self._soc_calculator.are_helpers_available()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._maybe_finalize_device_info()
        previous_available = self.available
        availability_changed = self._update_availability_state()
        value_changed = self._update_native_value()
        attributes_changed = self._update_extra_state_attributes()
        if availability_changed or previous_available != self.available or value_changed or attributes_changed:
            self.async_write_ha_state()

    def _resolve_remaining_inputs(self) -> tuple | None:
        """Collect the inputs needed to compute remaining-charge ETA.

        Returns a tuple (current_soc, target_soc, power_meas, battery_capacity,
        correction) when every input is present, otherwise None.
        """
        if not self._soc_calculator.are_helpers_available():
            return None
        power_meas = get_safe_value(self._updater.data, "powerMeas", float)
        energy_charged = self._get_energy_charged()
        if power_meas is None or energy_charged is None:
            return None
        # Reject a finite-but-impossible power outlier (e.g. 1e100) so a corrupt
        # payload can't make the ETA collapse to "< 1m" — the same ceiling the
        # Power sensor applies.
        if not 0 <= power_meas <= MAX_POWER_W:
            return None
        battery_capacity = self._soc_calculator.battery_capacity
        target_soc = self._soc_calculator.target_soc
        soc_correction = self._soc_calculator.soc_correction
        current_soc = self._soc_calculator.get_soc_percent(energy_charged)
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
        to update ``number.eveus_ev_charger_initial_soc`` before unplugging, since the
        charger starts a fresh session count on the next plug-in.
        """
        value = get_safe_value(self._updater.data, "sessionEnergy", float)
        # Reject a finite-but-impossible session-energy outlier (e.g. 1e100) so a
        # corrupt payload can't drive SOC %/kWh to a false full-battery reading;
        # matches the ceiling the Session Energy sensor applies.
        if value is None or not 0 <= value <= MAX_ENERGY_KWH:
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
    _requires_helpers = False

    def _get_sensor_value(self) -> Optional[float]:
        # If the charger has not yet reported sessionEnergy (cold start, offline
        # blip, or no session ever began), treat it as 0 delivered — SOC then
        # equals the user's Initial SOC. Prevents the entity from being
        # "unknown" the moment HA boots before the first successful poll.
        if self._session_energy_is_invalid():
            return None
        energy_charged = self._get_energy_charged() or 0.0
        result = self._soc_calculator.get_soc_kwh(energy_charged)
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
    _requires_helpers = False

    def _get_sensor_value(self) -> Optional[float]:
        # See EVSocKwhSensor._get_sensor_value — same Initial-SOC fallback.
        if self._session_energy_is_invalid():
            return None
        energy_charged = self._get_energy_charged() or 0.0
        result = self._soc_calculator.get_soc_percent(energy_charged)
        if result is not None:
            self._cached_value = result
        return self._cached_value


class TimeToTargetSocSensor(BaseEVHelperSensor):
    """Time to target SOC sensor."""

    ENTITY_NAME = "Time to Target SOC"
    _attr_icon = "mdi:timer"
    _requires_helpers = False

    def _get_sensor_value(self) -> str | None:
        """Time to target as a UI string, or None (unknown) when it can't be
        computed yet — SOC inputs not pushed, no Target SOC, or the charger
        isn't reporting power/SOC telemetry. Returning None drops any stale
        "2h 15m" instead of freezing it."""
        try:
            inputs = self._resolve_remaining_inputs()
            if inputs is None:
                self._cached_value = None
                return None
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
    # Available whenever online; reads None (via _resolve_remaining_inputs) when
    # target/helpers are missing, so the timestamp entity always exists.
    _requires_helpers = False

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

