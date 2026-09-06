"""EV-specific sensors with optional helper support."""
from __future__ import annotations

import logging
from datetime import datetime
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
from .const import (
    DEFAULT_SOC_CORRECTION,
    MAX_ENERGY_KWH,
    MAX_POWER_W,
    SESSION_ACTIVE_STATES,
    soc_update_signal,
)

_LOGGER = logging.getLogger(__name__)

# Both charge estimates are stated on this grid and damped by this step. They
# are two views of one calculation, and there is exactly ONE of them: the
# damped finish instant. Time to Target is projected off it, so the two can
# disagree only by that projection's own rounding — half a step — and no later
# change to one sensor can walk them apart the way two anchors did.
_ESTIMATE_STEP_MINUTES = 5
_ESTIMATE_STEP_SECONDS = _ESTIMATE_STEP_MINUTES * 60

# The single anchor, held on the updater (one per charger).
_ESTIMATE_ANCHOR_KEY = "finish_at"

# Share of the estimate the charger's own power swing is worth. Measured on the
# charger: 3504-3537 W across a session at a fixed current, so an estimate
# wanders by about half a percent either side. A band has to be WIDER than the
# swing it absorbs -- one that merely equals it opens on the extremes -- and the
# swing scales with the estimate, so the band does too.
_ESTIMATE_NOISE_FRACTION = 0.03


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
        # Outcome of the last external-SOC seed attempt in this plug-in cycle,
        # recorded here rather than on the number entity so diagnostics can
        # reach it through runtime_data. {"seeded": bool, "detail": str}.
        self.last_seed: dict = {}

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
            _LOGGER.debug("Error calculating SOC kWh: %s", err, exc_info=True)  # pragma: no mutate - log message text is display-only; exc_info kwarg (traceback capture) is not observed by any test
            return None

    def get_soc_percent(self, energy_charged: float) -> Optional[float]:
        """Battery SOC percent (rounded for display), or None when inputs missing."""
        if not self.are_helpers_available() or not self.battery_capacity:
            return None
        return calculate_soc_percent(
            self.initial_soc, self.battery_capacity, energy_charged, self._effective_correction()
        )

    def get_soc_percent_exact(self, energy_charged: float) -> Optional[float]:
        """Unrounded SOC percent for target/ETA logic, or None when inputs missing.

        The display percent (``get_soc_percent``) rounds to a whole number, which
        on a large battery can reach the target up to ~0.5% before the battery
        actually does — stopping a charge or reporting "target reached" early.
        Target comparisons and ETAs must use this exact value instead.
        """
        kwh = self.get_soc_kwh(energy_charged)
        if kwh is None or not self.battery_capacity:
            return None
        return max(0.0, min(100.0, kwh / self.battery_capacity * 100))


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

    @callback  # pragma: no mutate - HA callback-marker decorator, only sets _hass_callback for the runtime scheduler; no test observes it
    def _on_soc_input_changed(self) -> None:
        """Recompute immediately when a SOC input value is pushed."""
        previous_available = self.available
        availability_changed = self._update_availability_state()
        if not self._updater.available or not self._updater.last_update_success:
            if availability_changed or previous_available != self.available:
                self.async_write_ha_state()
            return
        value_changed = self._update_native_value()
        attributes_changed = self._update_extra_state_attributes()
        if availability_changed or value_changed or attributes_changed or previous_available != self.available:
            self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Available when online; SOC%/kWh additionally require inputs present."""
        if not super().available:
            return False
        if not self._requires_helpers:
            return True
        return self._soc_calculator.are_helpers_available()

    @callback  # pragma: no mutate - HA callback-marker decorator, only sets _hass_callback for the runtime scheduler; no test observes it
    def _handle_coordinator_update(self) -> None:
        self._maybe_finalize_device_info()
        previous_available = self.available
        availability_changed = self._update_availability_state()
        # A FAILED refresh notifies listeners too, with updater.data still
        # holding the previous payload. Recomputing from it would walk
        # time-anchored estimates (Charging Finish Time = now + stale
        # remaining) later on every failed poll, so only availability is
        # processed until a fresh successful payload arrives.
        if not self._updater.available or not self._updater.last_update_success:
            if availability_changed or previous_available != self.available:
                self.async_write_ha_state()
            return
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
        # Only an actively charging session has a meaningful ETA. Residual
        # standby power in Connected/Complete/Error states would otherwise
        # produce an absurd-but-plausible time-to-target and finish timestamp,
        # so outside an active session the power is treated as zero and the
        # sensors resolve to their "Not charging" / unknown states.
        state = get_safe_value(self._updater.data, "state", int)
        if state not in SESSION_ACTIVE_STATES:
            power_meas: float | None = 0.0  # pragma: no mutate - PEP 563 postponed evaluation: local-variable annotation is never evaluated at runtime, only the `= 0.0` assignment executes
        else:
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
        # Exact (unrounded) percent so Time-to-Target / Finish-Time agree with the
        # exact-kWh Energy-to-Target sensor instead of reporting "reached" early.
        current_soc = self._soc_calculator.get_soc_percent_exact(energy_charged)
        if None in (battery_capacity, target_soc, current_soc):
            return None
        return (current_soc, target_soc, power_meas, battery_capacity, soc_correction)

    def _anchors(self) -> dict:
        """The charger-wide store the single estimate anchor lives in."""
        anchors = getattr(self._updater, "_estimate_anchors", None)
        if anchors is None:
            anchors = {}
            self._updater._estimate_anchors = anchors
        return anchors

    def _damped_finish_timestamp(self, seconds, now: datetime) -> float | None:
        """The ONE damped quantity both charge estimates are views of.

        Returns the held finish instant as a Unix timestamp on the
        `_ESTIMATE_STEP_MINUTES` grid, or None when there is no estimate to
        state (which also drops the anchor, so a session that ends cannot seed
        the next one).

        Why an instant and not a duration, given that Time to Target displays a
        duration: an instant is what actually stays still. Damping the duration
        freezes a NUMBER while the clock keeps moving underneath it, which
        silently means a finish time sliding one minute later every minute —
        so the two sensors, damped separately, disagreed by up to 7 minutes on
        a two-hour charge and 17 on a ten-hour one, on a sawtooth that reset
        every time the duration re-anchored. Time to Target is now `held - now`
        instead, which costs it a row every five minutes while charging (it is
        counting down, which is its job) and buys the pair an agreement that
        holds by construction rather than by a test.

        The band is measured from the HELD instant rather than from the raw
        estimate: re-anchoring on the raw lands the anchor on a swing peak,
        leaving the opposite peak a full swing away. It is also wider than the
        swing rather than equal to it — one that merely equals the swing opens
        on the extremes. The charger's own power reading moves 3504-3537 W
        across a session at a fixed current, about half a percent either side,
        and that share of the answer grows with the answer, so the band is the
        larger of one and a half grid steps and `_ESTIMATE_NOISE_FRACTION` of
        the time still to run.
        """
        anchors = self._anchors()
        if seconds is None or seconds <= 0:
            anchors.pop(_ESTIMATE_ANCHOR_KEY, None)
            return None
        now_ts = now.timestamp()
        eta = now_ts + seconds
        held = anchors.get(_ESTIMATE_ANCHOR_KEY)
        band = max(
            _ESTIMATE_STEP_SECONDS * 1.5, _ESTIMATE_NOISE_FRACTION * seconds
        )
        # A held instant that has come due is re-anchored like any other stale
        # one: the stamp has to stay in the future, and a charge running past
        # its own estimate is exactly when it would not.
        if held is None or abs(eta - held) >= band or held <= now_ts:
            held = round(eta / _ESTIMATE_STEP_SECONDS) * _ESTIMATE_STEP_SECONDS
            if held <= now_ts:
                # Nearest-grid rounding can land on or behind `now` when only a
                # few minutes remain. Take the next grid point instead, which
                # is the same floor `calculate_remaining_time` has always
                # applied to the duration ("under 5 minutes the sensor still
                # reads 5m").
                held = (int(now_ts // _ESTIMATE_STEP_SECONDS) + 1) * _ESTIMATE_STEP_SECONDS
        anchors[_ESTIMATE_ANCHOR_KEY] = held
        return held

    def _forget_estimate(self) -> None:
        """Drop the held finish instant.

        Every exit that publishes no estimate goes through here, so a session
        that ends cannot seed the next one with the instant it was holding.
        One key for both sensors is what stops them drifting apart again: the
        finish stamp used to return early without clearing while Time to Target
        cleared by routing its `None` through its own separate damper.
        """
        self._anchors().pop(_ESTIMATE_ANCHOR_KEY, None)

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

    def _session_energy_or_zero(self) -> float | None:
        """Session kWh delivered, 0.0 before a session, None when unusable.

        A present-but-corrupt reading is always unusable. The field being
        absent entirely means 0 kWh delivered before a session starts, but
        during an ACTIVE session it is anomalous telemetry: falling back to 0
        would snap every from-initial-SOC figure back to the session start.
        """
        if self._session_energy_is_invalid():
            return None
        energy_charged = self._get_energy_charged()
        if energy_charged is None:
            state = get_safe_value(self._updater.data, "state", int)
            if state in SESSION_ACTIVE_STATES:
                return None
            energy_charged = 0.0
        return energy_charged


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
    # A battery level, not a meter: nothing accumulates here, so holding it
    # within 0.1 kWh loses nothing and drops the per-poll write while charging.
    _deadband = 0.1

    def _get_sensor_value(self) -> Optional[float]:
        # Outside a session an unreported sessionEnergy (cold start, offline
        # blip, or no session ever began) means 0 delivered — SOC then equals
        # the user's Initial SOC. Prevents the entity from being "unknown" the
        # moment HA boots before the first successful poll.
        energy_charged = self._session_energy_or_zero()
        if energy_charged is None:
            return None
        # None means the SOC helper inputs are missing (or a calc error), not a
        # transient poll blip — go unknown instead of freezing on a stale value.
        result = self._soc_calculator.get_soc_kwh(energy_charged)
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
        energy_charged = self._session_energy_or_zero()
        if energy_charged is None:
            return None
        # None means the SOC helper inputs are missing, not a transient poll
        # blip — go unknown instead of freezing on a stale value.
        result = self._soc_calculator.get_soc_percent(energy_charged)
        self._cached_value = result
        return self._cached_value

    def _update_extra_state_attributes(self) -> bool:
        """Report how Initial SOC — the anchor every SOC figure derives from —
        was last set.

        A stale hand-set Initial SOC skews every SOC reading with nothing on the
        dashboard to say so; this was previously visible only in a downloaded
        diagnostics file. `last_seed` is written on a plug-state change, on a
        successful seed, and once per cycle when seeding fails — never per poll
        — and this reports a change only when the values actually differ, so it
        adds no recorder churn.
        """
        last_seed = getattr(self._soc_calculator, "last_seed", None) or {}
        attributes = {
            "soc_anchor_seeded": bool(last_seed.get("seeded")),
            "soc_anchor": last_seed.get("detail") or "set manually",
        }
        # getattr with a default: HA's cached-property machinery raises rather
        # than returning None when _attr_extra_state_attributes was never set.
        if attributes == getattr(self, "_attr_extra_state_attributes", None):
            return False
        self._attr_extra_state_attributes = attributes
        return True


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
                self._forget_estimate()
                self._cached_value = None
                return None
            seconds = calculate_remaining_seconds(*inputs)
            now = dt_util.utcnow()
            held = self._damped_finish_timestamp(seconds, now)
            raw_minutes = (
                round(seconds / 60, 0) if seconds is not None and seconds > 0 else None
            )
            if held is None:
                # No estimate: `calculate_remaining_time` states why itself
                # ("Not charging", "unavailable", target reached).
                minutes = None
            elif raw_minutes < 1:
                # Under a minute left is stated plainly. The anchor is the next
                # grid point by then, so projecting off it would say "5m" while
                # the cable is seconds from done — the floor below is a floor,
                # not a lie.
                minutes = raw_minutes
            else:
                # The projection, and the whole point of one anchor: this is
                # the same instant Charging Finish Time publishes, expressed as
                # what is left of it, and `calculate_remaining_time` snaps it
                # onto the shared grid.
                #
                # The floor is the one place the two are allowed to part, and
                # it predates the damping: `calculate_remaining_time` promises
                # it "never rounds down to 0m — under 5 minutes the sensor
                # still reads 5m until it drops below one minute". Inside the
                # final grid step the anchor sits on the next boundary, which
                # can be seconds away, so the raw projection would render
                # "< 1m" for a charge with minutes left. Hold the floor and let
                # the stamp state the boundary exactly.
                minutes = max(
                    _ESTIMATE_STEP_MINUTES, (held - now.timestamp()) / 60
                )
            result = calculate_remaining_time(*inputs, minutes_override=minutes)
            self._cached_value = result
            return result

        except Exception as err:
            # Third exit that publishes no estimate, so it drops the anchor like
            # the other two: a hold left behind by a failed computation is
            # measured against on the next successful one.
            self._forget_estimate()
            _LOGGER.debug(
                "Error calculating time to target for %s: %s",  # pragma: no mutate - pure log-message text, arguments unchanged
                self.unique_id,
                err,
                exc_info=True,  # pragma: no mutate - log-verbosity kwarg only (traceback capture); no test observes it
            )
            # Matches the docstring: drop any stale value on failure instead of
            # freezing it (this class's own inputs-missing branch above does
            # the same via _cached_value = None).
            self._cached_value = None
            return None


class EnergyToTargetSocSensor(BaseEVHelperSensor):
    """Grid energy still needed to reach the Target SOC.

    Converts the remaining battery energy (Target SOC minus current SOC)
    back into grid kWh by undoing the SOC correction, so the value matches
    what the meter will actually count. Unknown until the SOC inputs and a
    Target SOC are set; 0 once the target is reached.
    """

    ENTITY_NAME = "Energy to Target SOC"
    # No device class: this is energy still NEEDED from the grid, not energy
    # currently stored — ENERGY_STORAGE would mislabel it, and ENERGY requires
    # TOTAL/TOTAL_INCREASING. Plain kWh + MEASUREMENT is the honest contract.
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:battery-arrow-up"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _requires_helpers = False
    # A forecast displayed to one decimal — a 0.25 kWh step is finer than the
    # estimate's own accuracy, so it costs no information.
    _deadband = 0.25

    def _remaining_grid_kwh(self) -> Optional[float]:
        """Grid kWh to target, 0.0 at target, or None when not computable."""
        calc = self._soc_calculator
        if not calc.are_helpers_available() or calc.target_soc is None:
            return None
        energy_charged = self._session_energy_or_zero()
        if energy_charged is None:
            return None
        # Work in kWh, not the whole-percent rounded SOC: on a large battery a
        # rounded-up percent would zero the estimate with real energy missing.
        current_kwh = calc.get_soc_kwh(energy_charged)
        battery_capacity = calc.battery_capacity
        if current_kwh is None or not battery_capacity:
            return None
        remaining_battery_kwh = calc.target_soc * battery_capacity / 100 - current_kwh
        if remaining_battery_kwh <= 0:
            return 0.0
        correction = calc.soc_correction
        if not 0 <= correction < 100:
            return None
        return remaining_battery_kwh / (1 - correction / 100)

    def _get_sensor_value(self) -> Optional[float]:
        remaining = self._remaining_grid_kwh()
        return None if remaining is None else round(remaining, 2)


class CostToTargetSocSensor(EnergyToTargetSocSensor):
    """Forecast cost of reaching the Target SOC at the active tariff rate.

    Prices the remaining grid energy with the charger's currently active
    tariff (`activeTarif` -> `tarif` / `tarifAValue` / `tarifBValue`), so the
    forecast drifts only if the active rate changes before the session ends.
    """

    ENTITY_NAME = "Cost to Target SOC"
    # Monetary semantics, but no state_class: a forecast that counts down to
    # zero would only pollute long-term statistics.
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = None
    _attr_native_unit_of_measurement = "UAH"
    _attr_icon = "mdi:cash-clock"
    _attr_suggested_display_precision = 0
    # Displayed with no decimals at all, so anything under a hryvnia was a
    # recorder row nobody could see.
    _deadband = 1.0

    def _get_sensor_value(self) -> Optional[float]:
        from .sensor_definitions import get_active_rate_cost

        remaining = self._remaining_grid_kwh()
        if remaining is None:
            return None
        if remaining == 0:
            # At target the cost is exactly zero regardless of tariff
            # telemetry; missing tariff data must not blank a known value.
            return 0.0
        rate = get_active_rate_cost(self._updater, self.hass)
        if rate is None:
            return None
        return round(remaining * rate, 2)


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
                self._forget_estimate()
                return None
            seconds = calculate_remaining_seconds(*inputs)
            # The anchor IS this sensor's value — Time to Target is the same
            # instant expressed as what is left of it. Damping an estimate
            # re-derived from a fluctuating power reading is what stops a stamp
            # sitting on a grid edge alternating between the two adjacent
            # buckets on every poll, which both flooded the recorder and
            # re-fired any automation waiting on this timestamp.
            held = self._damped_finish_timestamp(seconds, dt_util.utcnow())
            if held is None:
                # None = not charging / invalid; 0 = target reached
                return None
            # Built from the epoch value rather than shifted from `now`: the
            # anchor is an exact multiple of the grid, so rounding it to a
            # whole second lands on the boundary with no float dust to truncate
            # (a stamp a microsecond short of the minute, blanked to :00,
            # would read a whole minute early).
            return dt_util.utc_from_timestamp(round(held))
        except Exception as err:
            # Same third exit as Time to Target's — see there.
            self._forget_estimate()
            _LOGGER.debug(
                "Error calculating finish time for %s: %s",  # pragma: no mutate - pure log-message text, arguments unchanged
                self.unique_id,
                err,
                exc_info=True,  # pragma: no mutate - log-verbosity kwarg only (traceback capture); no test observes it
            )
            return None
