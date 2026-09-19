"""Sensor definitions and factory for Eveus integration."""
from __future__ import annotations

import logging
import math
from typing import Any, Callable, Dict, Final, Optional
from datetime import datetime
from functools import lru_cache

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.util import dt as dt_util

from .common_base import EveusSensorBase
from .const import (
    poll_failure_signal,
    get_charging_state,
    get_error_state,
    get_normal_substate,
    CHARGING_STATES,
    DEVICE_STATE_CHARGING,
    DEVICE_STATE_ERROR,
    DEVICE_STATE_STANDBY,
    ERROR_STATES,
    NORMAL_SUBSTATES,
    RATE_STATES,
    SESSION_ACTIVE_STATES,
    ERROR_LOG_RATE_LIMIT,
    LEGACY_RAW_STATE_KEY,
    MAX_SCHEDULE_ENERGY_KWH,
    MAX_SESSION_TIME_SECONDS,
    TIME_DRIFT_TOLERANCE_SECONDS,
    TIME_DRIFT_QUANTUM_SECONDS,
)
from .utils import (
    RateLog,
    format_duration,
    get_local_wall_clock_seconds,
)

_LOGGER = logging.getLogger(__name__)
_MAX_ERROR_LOG_KEYS = 64
_SENSOR_FUNCTION_LOG = RateLog(max_keys=_MAX_ERROR_LOG_KEYS)
ICON_FLASH = "mdi:flash"
ICON_CURRENT_AC = "mdi:current-ac"
ICON_CURRENCY_UAH = "mdi:currency-uah"
UNIT_UAH_PER_KWH = "₴/kWh"
UNIT_UAH = "UAH"
# Upper sanity ceilings for live telemetry no longer live here: every one of
# them is now a bound in snapshot._FIELDS, applied once when the payload is
# parsed. A getter below states presentation only — precision, a unit
# transform, a display deadband.
_MAX_SCHEDULE_KWH = MAX_SCHEDULE_ENERGY_KWH
_RATE_COST_KEYS: Final = {0: "tarif", 1: "tarifAValue", 2: "tarifBValue"}


def _should_log_error(function_name: str) -> bool:
    """Check if a module-level sensor helper should log an error."""
    return _SENSOR_FUNCTION_LOG.should_log(ERROR_LOG_RATE_LIMIT, function_name)


class EveusSensorEntityDescription(SensorEntityDescription, frozen_or_thawed=True):
    """Sensor description for factory-built Eveus sensors.

    Extends the standard HA fields (icon/device_class/state_class/
    native_unit_of_measurement/suggested_display_precision/entity_category/
    options) with what the factory needs beyond them: how to compute a
    reading, an optional attributes function, and the handful of per-sensor
    behavioural flags below.
    """

    value_fn: Callable = None
    attributes_fn: Optional[Callable] = None  # pragma: no mutate - default only reached via `if not self._spec.attributes_fn:`; None/"" both falsy
    tracks_reset: bool = False  # pragma: no mutate - default only reached via `if self.tracks_reset`; False/None both falsy
    # Sensors whose value DESCRIBES connectivity must stay readable while the
    # poll is failing — that is exactly when their data matters.
    available_when_offline: bool = False  # pragma: no mutate - only reached via truthy checks; None/False both falsy
    # The published value comes from a hold kept on the UPDATER, which a reload
    # throws away — so it has to be seeded from the restored state or the sensor
    # counts backwards after a restart. See `_seed_session_hold`.
    restores_session_hold: bool = False  # pragma: no mutate - only reached via truthy checks; None/False both falsy
    # Churn damping for a reading that dithers between polls, applied by the
    # entity (`EveusSensorBase._deadband`) rather than here — see
    # `_make_value_getter`'s docstring for why the getter itself stays pure.
    deadband: Optional[float] = None  # pragma: no mutate - default only reached via `if self._deadband is not None`; None/0 both leave every reading published verbatim


class OptimizedEveusSensor(EveusSensorBase):
    """High-performance templated sensor."""

    def __init__(self, updater, spec: EveusSensorEntityDescription, device_number: int = 1):
        """Initialize sensor from spec."""
        self.ENTITY_NAME = spec.name
        super().__init__(updater, device_number)

        self._spec = spec
        # icon/device_class/state_class/native_unit_of_measurement/
        # suggested_display_precision/entity_category/options all resolve
        # through this — HA's own Entity/SensorEntity properties fall back to
        # entity_description when the matching _attr_* is absent.
        self.entity_description = spec
        self._error_log = RateLog(max_keys=_MAX_ERROR_LOG_KEYS)
        self._deadband = spec.deadband
        self._attr_extra_state_attributes = {}

    @property
    def available(self) -> bool:
        """Connectivity-describing sensors stay available while polls fail."""
        if self._spec.available_when_offline:
            return True
        return super().available

    def _update_native_value(self) -> bool:
        """Refresh the value, mirroring WiFi Signal's damped reading.

        The Connection Quality attribute reads this mirror instead of
        recomputing RSSI itself, so the two can never publish a different
        damped value for the same underlying reading.
        """
        changed = super()._update_native_value()
        if self._spec.key == "wifi_signal":
            self._updater._wifi_rssi_damped = self._attr_native_value
        return changed

    async def async_added_to_hass(self) -> None:
        """Restore the updater-side hold this sensor's value is built on.

        The base class computes and caches the first value inside
        `super().async_added_to_hass()`, so a seed applied afterwards fixes
        every poll EXCEPT the one the reload was about: the entity would come
        back showing the coarse floor — the exact 4:59 regression this seeding
        exists to remove — and only correct itself on the next poll, minutes
        away at the idle cadence. Seed first, then let the base read.
        """
        if self._spec.restores_session_hold:
            self._seed_session_hold(await self.async_get_last_state())
        await super().async_added_to_hass()
        if self._spec.available_when_offline:
            # The link metric moves on every failed poll, which HA does not
            # announce; the value is a whole percent of a 20-poll window, so
            # an outage writes at most 20 rows before it settles at 0.
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    poll_failure_signal(self._updater.config_entry.entry_id),
                    self._handle_coordinator_update,
                )
            )

    def _seed_session_hold(self, state) -> None:
        """Re-arm `_session_time_seconds` from the state HA kept for us.

        The never-count-backwards hold lives on the updater, so a reload drops
        it and the first reading afterwards takes the coarse idle floor —
        up to 4:59 BEHIND what the sensor last published. Live 2026-09-05: the
        charger reported 509 095 s (5d 21h 24m) and the sensor came back from a
        restart reading 5d 21h 20m.

        Seeded from the `duration_seconds` attribute rather than by parsing the
        display string back: it is the exact stepped number the hold works in.
        A value that is missing, non-numeric or outside the range the getter
        itself accepts is discarded — a corrupt restore must not become the
        floor every later reading is measured against. Nothing here can
        resurrect a finished session: the hold only applies while the charger's
        own counter is still ahead of it, so a cable pulled while HA was down
        starts from zero as usual.
        """
        if state is None:
            return
        try:
            seconds = int((state.attributes or {}).get("duration_seconds"))
        except (TypeError, ValueError):
            return
        if not 0 <= seconds <= MAX_SESSION_TIME_SECONDS:
            return
        self._updater._session_time_seconds = seconds

    def _should_log_error(self, function_name: str) -> bool:
        """Check if we should log errors for a function (rate limited)."""
        return self._error_log.should_log(ERROR_LOG_RATE_LIMIT, function_name)

    def _get_sensor_value(self) -> Any:
        """Return computed sensor value from coordinator data."""
        if not self._updater.available and not self._spec.available_when_offline:
            return None

        try:
            return self._spec.value_fn(self._updater, self.hass)
        except Exception as err:
            if self._should_log_error(f"sensor_{self._spec.key}"):
                _LOGGER.debug("Error getting value for %s: %s", self.name, type(err).__name__)  # pragma: no mutate - log message text/exc_info; nothing asserts on either
            return None

    def _update_extra_state_attributes(self) -> bool:
        """Refresh cached attributes from coordinator data."""
        if not self._spec.attributes_fn:
            return False
        previous_attrs = self._attr_extra_state_attributes
        attrs: Dict[str, Any] = {}  # pragma: no mutate - always normalized via `attrs or {}` below; None/{} indistinguishable
        try:
            # Offline-capable sensors (Connection Quality) still compute their
            # attributes while polls fail: the success-rate/latency/status come
            # from the rolling connectivity metric, which is exactly what the
            # user needs to see during an outage. The attribute function itself
            # drops any stale payload-derived field (e.g. RSSI) when offline.
            if self._updater.available or self._spec.available_when_offline:
                attrs = self._spec.attributes_fn(self._updater, self.hass)
            elif self._in_availability_grace:
                # An attribute change writes a recorder row exactly like a
                # state change, and dropping the attributes on a missed poll
                # writes one on the way down and another on the way back up.
                # Hold them for the same window the value is held for.
                return False
        except Exception as err:
            if self._should_log_error(f"attributes_{self._spec.key}"):
                _LOGGER.debug(
                    "Error getting attributes for %s: %s",  # pragma: no mutate - log message text only
                    self.name,
                    type(err).__name__,
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

    def __init__(self, updater, spec: "EveusSensorEntityDescription", device_number: int = 1) -> None:  # pragma: no mutate - default unreachable: create_sensor always passes device_number explicitly
        """Initialize the cost sensor with reset tracking state."""
        super().__init__(updater, spec, device_number)
        self._prev_cost_value: Optional[float] = None  # pragma: no mutate - unreachable: first _update_native_value always short-circuits on `_attr_last_reset is None` before reading this default
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
            if isinstance(parsed, datetime):
                self._attr_last_reset = parsed
        try:
            restored = float(state.state)
        except (TypeError, ValueError):
            restored = None
        self._prev_cost_value = (
            restored if restored is not None and math.isfinite(restored) else None
        )


def create_sensor(
    spec: EveusSensorEntityDescription, updater, device_number: int = 1
) -> OptimizedEveusSensor:
    """Create the sensor entity a spec describes.

    A plain function, not a method on the spec: `frozen_or_thawed=True`
    descriptions are instantiated as a separate generated dataclass behind
    the scenes (see `EveusSwitchEntityDescription` and its siblings), so a
    method defined in the class body is never reachable on the actual
    instance.
    """
    cls = MonetaryCostSensor if spec.tracks_reset else OptimizedEveusSensor
    return cls(updater, spec, device_number)


# =============================================================================
# Value helper
# =============================================================================

# =============================================================================
# Value getter factories — replace ~20 identical functions
# =============================================================================

def _deadband_anchor_store(updater) -> dict:
    """The per-updater store `_held_latency` anchors its display grid on.

    The type guard is load-bearing rather than decorative: anything that is
    not the store (an absent attribute, or a stand-in that answers every
    attribute) must be replaced with a real dict, because the alternative is
    arithmetic against a non-number, which the caller would report as a
    failed reading.
    """
    anchors = getattr(updater, "_deadband_anchors", None)
    if not isinstance(anchors, dict):
        anchors = {}
        updater._deadband_anchors = anchors
    return anchors


def _make_value_getter(
    key: str,
    precision: int = 0,
    transform: Callable = None,
):
    """Factory for simple data getter functions.

    Conversion and the physical bounds happen once, in the snapshot: a reading
    that is absent, corrupt or impossible arrives here as ``None``. What is
    left is presentation — an optional unit ``transform`` and a rounding
    ``precision``. Churn damping is NOT the getter's job: it lives on the
    entity (`EveusSensorBase._deadband`, set from `EveusSensorEntityDescription.deadband`), the
    one place that already holds a per-entity "last published value" to damp
    against.
    """
    def getter(updater, hass):
        # Availability, not validity: a failing poll publishes nothing and the
        # entity layer holds the last reading through its grace window.
        if not updater.available:
            return None
        value = updater.snapshot.get(key)
        if value is None:
            return None
        if transform:
            value = transform(value)
        return round(value, precision)
    return getter


def _read_int(updater, key: str) -> Optional[int]:
    """A whole-number field from the latest good poll, or None when offline."""
    if not updater.available:
        return None
    return updater.snapshot.get_int(key)


def _make_enum_getter(key: str, mapping: dict[int, str]):
    """Read an int key and map it to a label, else None.

    The getter carries its own label set on ``.options`` so a spec can declare
    ``options=getter.options`` instead of restating the values — a restated
    list drifts out of sync with the mapping, and any label missing from it is
    silently rejected by Home Assistant at write time.
    """
    def getter(updater, hass) -> Optional[str]:
        return mapping.get(_read_int(updater, key))
    getter.options = tuple(dict.fromkeys(mapping.values()))
    return getter


# Measurement getters
get_voltage = _make_value_getter(
    "voltMeas1", precision=0
)
get_current = _make_value_getter(
    "curMeas1", precision=1
)
get_power = _make_value_getter(
    "powerMeas", precision=1
)
# Energy getters
get_session_energy = _make_value_getter(
    "sessionEnergy", precision=2)
get_total_energy = _make_value_getter(
    "totalEnergy", precision=2)
get_counter_a_energy = _make_value_getter(
    "IEM1", precision=2)
get_counter_b_energy = _make_value_getter(
    "IEM2", precision=2)

# Cost getters.
# Firmware contract:
#   * `tarif*` fields are reported in HUNDREDTHS of a currency unit (kop/cent),
#     so they must be divided by 100 to get the per-kWh price.
#   * `IEM1_money`, `IEM2_money`, `sessionMoney` are already in WHOLE currency
#     units — DO NOT divide. Verified against R3.05.2 firmware.
_div100 = lambda v: v / 100
get_counter_a_cost = _make_value_getter(
    "IEM1_money", precision=2)
get_counter_b_cost = _make_value_getter(
    "IEM2_money", precision=2)
get_primary_rate_cost = _make_value_getter(
    "tarif", precision=2, transform=_div100)
get_rate2_cost = _make_value_getter(
    "tarifAValue", precision=2, transform=_div100)
get_rate3_cost = _make_value_getter(
    "tarifBValue", precision=2, transform=_div100)

# Temperature getters
# 2 degrees, not 1: these are whole-degree readings that alternate between two
# adjacent values for hours, and a 1 degree band is no band at all — the next
# distinct value is already 1 away.
get_box_temperature = _make_value_getter(
    "temperature1",
    precision=0,
)
get_plug_temperature = _make_value_getter(
    "temperature2",
    precision=0,
)

# Other diagnostic getters
# The CR2032 coin cell reads ~3 V; reject non-positive and physically impossible
# values (matching the low-battery Repairs tracker's plausible window) so a
# firmware glitch can't push junk into Battery Voltage history.
get_battery_voltage = _make_value_getter(
    "vBat",
    precision=2,
)
get_leak_current = _make_value_getter(
    "leakValue", precision=0)
get_leak_current_peak = _make_value_getter(
    "leakValueH", precision=0)
# RSSI is reported in dBm — physically always ≤ 0 (typical floor ~ −120 dBm).
# 5 dBm: measured on the live charger, RSSI wanders across ~7 dBm with the link
# unchanged, so a 3 dBm band still published one reading in seven — and this
# value is mirrored into the Connection Quality attributes, which made it the
# single largest source of recorder rows this integration produced.
get_wifi_rssi = _make_value_getter(
    "RSSI", precision=0
)

# 3-phase per-phase getters (only registered when entry is configured for 3 phases)
# Same telemetry as phase 1, so the same damping — otherwise a 3-phase entry
# keeps the per-poll churn the single-phase one just lost.
get_current_phase_2 = _make_value_getter(
    "curMeas2", precision=1
)
get_current_phase_3 = _make_value_getter(
    "curMeas3", precision=1
)
get_voltage_phase_2 = _make_value_getter(
    "voltMeas2", precision=0
)
get_voltage_phase_3 = _make_value_getter(
    "voltMeas3", precision=0
)


# =============================================================================
# State-based getters (need custom logic)
# =============================================================================

def get_charger_state(updater, hass) -> Optional[str]:
    """Get charger state.

    Firmware 1.x (MCU_SW_version 151, GitHub issue #11) reports state values
    outside CHARGING_STATES (observed: 20). ``validate_main_payload`` accepts
    any 0-255 state, but the sensor is a closed-options ENUM — any value not
    in the options list is rejected by Home Assistant at write time, leaving
    the sensor stuck at `unknown`. So unmapped values must collapse to the
    plain "Unknown" option; the actual code is preserved in the `raw_state`
    attribute (see ``get_charger_state_attributes``) and logged once per
    distinct value (RateLog), not once per poll.
    """
    state_value = _read_int(updater, "state")
    if state_value is None:
        return None
    if state_value not in CHARGING_STATES:
        if _SENSOR_FUNCTION_LOG.should_log(ERROR_LOG_RATE_LIMIT, ("unknown_state", state_value)):  # pragma: no mutate - opaque rate-limit cache key text, never surfaced
            _LOGGER.warning("Eveus reported unrecognized device state: %s", state_value)  # pragma: no mutate - log message TEXT only
    return get_charging_state(state_value)


def get_charger_state_attributes(updater, hass) -> dict:
    """Expose the raw device-state code when the visible state hides it.

    Two cases carry a code the enum value can't show: an unmapped state
    (rendered as plain "Unknown") and a firmware-1.x state translated to its
    modern equivalent by the coordinator (original kept under
    LEGACY_RAW_STATE_KEY). A plain mapped state adds no attribute.
    """
    # Synthetic key, written by the coordinator's legacy translation — it is
    # not a charger field, so it is read from the raw payload, not the parse.
    raw_legacy = updater.snapshot.raw.get(LEGACY_RAW_STATE_KEY)
    if raw_legacy is not None:
        return {"raw_state": raw_legacy}
    state_value = _read_int(updater, "state")
    if state_value is not None and state_value not in CHARGING_STATES:
        return {"raw_state": state_value}
    return {}


def get_charger_substate(updater, hass) -> Optional[str]:
    """Get charger substate.

    Returns None when the device state itself is outside the known domain —
    otherwise a stray firmware state would be labelled with normal-mode substate
    text and look like a plausible diagnostic reason.
    """
    state = _read_int(updater, "state")
    substate = _read_int(updater, "subState")
    if None in (state, substate):
        return None
    if state not in CHARGING_STATES:
        return None
    if state == 7:
        # In the Error state, subState 0 is not a real "No Error" code — it means
        # the firmware reported no/zero fault code alongside an error state, which
        # is contradictory. Surface unknown (matching the safety path) instead of
        # the misleading "No Error" label.
        if substate == 0:
            return None
        return get_error_state(substate)
    return get_normal_substate(substate)


# The two reasons both schedule slots share — named so schedule 1 and 2 cannot
# drift into two differently-spelled versions of the same reason.
_REASON_WAITING_FOR_SCHEDULE: Final[str] = "Waiting for Schedule"
_REASON_SCHEDULE_ENERGY_LIMIT: Final[str] = "Schedule Energy Limit Reached"

NOT_CHARGING_REASON_OPTIONS: Final[tuple[str, ...]] = (
    "Charging",
    "Starting Up",
    "Cable Not Connected",
    "Waiting for Car",
    "Charge Complete",
    "Stopped by User",
    "Energy Limit Reached",
    "Time Limit Reached",
    "Cost Limit Reached",
    _REASON_WAITING_FOR_SCHEDULE,
    _REASON_SCHEDULE_ENERGY_LIMIT,
    "Waiting for Activation",
    "Paused by Adaptive Mode",
    "Paused",
    "Controlled by OCPP",
    "Error",
    "Unknown",
)

# subState meaning while the charger is Connected (3) or Paused (6) — the
# reason it is holding off rather than delivering current. Schedule 1 and 2
# collapse to one reason: which schedule fired is in the Schedule sensors.
_SUBSTATE_REASONS: Final[Dict[int, str]] = {
    1: "Stopped by User",
    2: "Energy Limit Reached",
    3: "Time Limit Reached",
    4: "Cost Limit Reached",
    5: _REASON_WAITING_FOR_SCHEDULE,
    6: _REASON_SCHEDULE_ENERGY_LIMIT,
    7: _REASON_WAITING_FOR_SCHEDULE,
    8: _REASON_SCHEDULE_ENERGY_LIMIT,
    9: "Waiting for Activation",
    10: "Paused by Adaptive Mode",
}


def _reads_modern_codes(updater) -> bool:
    """Whether this updater's subState follows the modern maps.

    The real coordinator answers this itself: its `is_modern_firmware` already
    ORs its sticky verdict (set the first time the firmware marker is seen,
    never cleared) with the current payload, so one reply that omits verFWMain
    cannot downgrade the reason. The payload fallback below is only for the
    lightweight updater doubles in the tests, which have no such property.
    """
    sticky = getattr(updater, "is_modern_firmware", None)
    if sticky is not None:
        return bool(sticky)
    return updater.snapshot.modern_firmware


def get_not_charging_reason(updater, hass) -> Optional[str]:
    """Answer "why is the charger not charging right now" in one value.

    State and Substate together already carry this, but reading them takes
    knowing which substate texts apply in which state. This folds both into a
    single closed set of reasons an automation can match on directly.
    """
    state = _read_int(updater, "state")
    if state is None:
        return None
    # Same closed-ENUM constraint as get_charger_state: an unmapped firmware
    # state must collapse to "Unknown" rather than be labelled with substate
    # text that does not apply to it.
    if state not in CHARGING_STATES:
        return "Unknown"
    if state == DEVICE_STATE_CHARGING:
        return "Charging"
    if state in (0, 1):
        return "Starting Up"
    if state == DEVICE_STATE_STANDBY:
        return "Cable Not Connected"
    if state == DEVICE_STATE_ERROR:
        return "Error"
    # OCPP hands start/stop to the backend or the vendor app, so no limit below
    # can be what is holding the session back — nothing HA does will start one
    # until it is switched off. Named ahead of those limits because it is the
    # only reason here that points at a setting the user has to change.
    if _read_int(updater, "ocppEnabled"):
        return "Controlled by OCPP"
    # Firmware keeps subState alive in state 5, so 9 there is not a finished
    # session — it is the charger holding for an external start command.
    if _reads_modern_codes(updater) and _read_int(updater, "subState") == 9:
        return _SUBSTATE_REASONS[9]
    if state == 5:
        return "Charge Complete"
    # Only modern firmware's subState follows NORMAL_SUBSTATES. Firmware 1.x
    # (GitHub issue #11) has its own codes, so reading one there would name a
    # confident but arbitrary reason; fall through to the state-derived answer,
    # which the coordinator's legacy translation already made correct.
    if _reads_modern_codes(updater):
        substate = _read_int(updater, "subState")
        reason = _SUBSTATE_REASONS.get(substate)
        if reason is not None:
            return reason
        # Same rule the state branch above follows: a code we cannot name is
        # not the same as no code. subState 0 really does mean "no limits",
        # but an unmapped non-zero one means some limit IS active — saying
        # "nothing is holding it back" there would be a confident lie. A
        # missing field is neither; it falls through as absent data.
        if substate not in (None, 0):
            return "Unknown"
    # No limit is holding it back: Connected means the car has not asked for
    # current yet, Paused means the charger itself is idling.
    return "Waiting for Car" if state == 3 else "Paused"


def get_not_charging_reason_attrs(updater, hass) -> dict:
    """Expose the fault name in the Error state plus the raw suspend word."""
    attrs: dict = {}
    state = _read_int(updater, "state")
    substate = _read_int(updater, "subState")
    # subState 0 in the Error state is the contradictory "no fault code with an
    # error" case get_charger_substate already blanks — no name to report. A
    # firmware-1.x fault code is not an ERROR_STATES index at all, so it gets
    # no name either; suspendErrors below is a raw word and stays either way.
    if (
        state == DEVICE_STATE_ERROR
        and substate not in (None, 0)
        and _reads_modern_codes(updater)
    ):
        attrs["error"] = get_error_state(substate)
    suspend = _read_int(updater, "suspendErrors")
    if suspend:
        attrs["suspend_errors"] = suspend
    return attrs


get_ground_status = _make_enum_getter("ground", {1: "Connected", 0: "Not Connected"})


# The charger counts a session from PLUG-IN, not from the start of charging,
# and stops only when the cable comes out — so a car left connected after
# Charge Complete kept this figure ticking, one recorder row per minute, for a
# duration nobody is reading any more. While a charge is actually running the
# minute is what the user is watching; outside one, a coarser step costs
# nothing.
_SESSION_TIME_STEP_CHARGING_SECONDS: Final[int] = 60
_SESSION_TIME_STEP_IDLE_SECONDS: Final[int] = 300


def _get_session_seconds(updater) -> Optional[int]:
    """Session duration, stepped by whether a charge is actually running.

    Shared by the state and its mirroring attribute so the two grids cannot
    drift apart — an attribute writes a recorder row exactly like a state does.
    """
    # A negative duration is physically impossible and an absurd one (corrupt
    # RTC / counter) would render an overlong state string; the snapshot
    # rejects both, so an unusable duration arrives here as None.
    seconds = _read_int(updater, "sessionTime")
    if seconds is None:
        return None
    state = _read_int(updater, "state")
    step = (
        _SESSION_TIME_STEP_CHARGING_SECONDS
        if state in SESSION_ACTIVE_STATES
        else _SESSION_TIME_STEP_IDLE_SECONDS
    )
    stepped = seconds - seconds % step
    last = getattr(updater, "_session_time_seconds", None)
    # Charging states the minute and standby the five, so the moment a charge
    # ends the coarser step would drag the published figure back down by up to
    # four minutes. Hold it instead — but only while the charger's own counter
    # is still ahead of it, so unplugging (which resets that counter) starts
    # the next session from zero rather than from a stale hold.
    # NOT pragma'd, deliberately, even though `stepped <` vs `stepped <=` is
    # unobservable (at equality the assignment below is a no-op): mutmut's
    # pragma is line-level, and the UPPER comparison on this same line is
    # load-bearing -- silencing the line would silence that too. The equivalent
    # mutant is carried in .github/mutation-baseline.json instead.
    if last is not None and stepped < last <= seconds:
        stepped = last
    updater._session_time_seconds = stepped
    return stepped


def get_session_time(updater, hass) -> Optional[str]:
    """Get formatted session time."""
    seconds = _get_session_seconds(updater)
    return None if seconds is None else format_duration(seconds)


def get_session_time_attrs(updater, hass) -> dict:
    """Get session time attributes."""
    if not updater.available:
        return {}
    seconds = _get_session_seconds(updater)
    # Mirror the state getter's bounds and its grid: an absurd duration must not
    # leak into the attribute while the visible state already reads unknown, and
    # a per-poll-incrementing second count made this sensor write every poll
    # while its state sat still — the exact churn the coarser grid exists to
    # avoid.
    return {} if seconds is None else {"duration_seconds": seconds}


def get_time_drift(updater, hass) -> Optional[int]:
    """Charger wall clock minus HA local wall clock, in seconds (0 = in sync).

    Replaces the old System Time clock readout: a ticking clock changes state
    on every poll and floods the recorder, while the drift is the only part
    that carries diagnostic value (it pairs with the Sync Time button and the
    Time Zone select). Wall clocks are compared — not UTC — so a wrong Time
    Zone select or a DST mismatch shows up as a whole-hour drift instead of
    being cancelled out. Drift within TIME_DRIFT_TOLERANCE_SECONDS reads as
    exactly 0; larger drifts snap to a TIME_DRIFT_QUANTUM_SECONDS grid. The
    last reported value is kept on the updater as hysteresis: a raw drift
    hovering at a band boundary (e.g. 5 <-> 6 s) would otherwise alternate
    between adjacent grid values on every poll, recreating the recorder
    flooding this sensor exists to avoid.
    """
    try:
        charger_wall = updater.snapshot.charger_wall_clock_s
        if charger_wall is None:
            return None
        drift = charger_wall - get_local_wall_clock_seconds()
        # Compute the quantized candidate FIRST, with the in-sync tolerance band
        # resolving to exactly 0. Hysteresis (keep the last report when the raw
        # drift only jitters within one grid step) is then applied only to a
        # non-zero candidate — so the zero/tolerance band always clears a stale
        # non-zero drift once the clock is synchronized (e.g. 30 s -> 0 s).
        # pragma: no mutate below - equivalent, and provably so: the quantum
        # rounds every |drift| up to half of itself (15 s) to zero already,
        # so for any tolerance under that this branch is a fast path to the
        # answer the else-branch computes anyway. `<` and `<=` cannot be
        # told apart at 5 s, and neither can the constant's exact value.
        # test_time_drift_tolerance_band_is_inclusive_at_both_signs asserts
        # the tolerance stays under half a quantum, so this stops being
        # true loudly rather than silently.
        if abs(drift) <= TIME_DRIFT_TOLERANCE_SECONDS:  # pragma: no mutate - see the note above
            candidate = 0
        else:
            candidate = (
                round(drift / TIME_DRIFT_QUANTUM_SECONDS) * TIME_DRIFT_QUANTUM_SECONDS
            )
        last = getattr(updater, "_time_drift_last_report", None)
        if (
            candidate != 0
            and last is not None
            and abs(drift - last) <= TIME_DRIFT_QUANTUM_SECONDS
        ):
            candidate = last
        updater._time_drift_last_report = candidate
        return candidate
    except Exception as err:
        if _should_log_error("get_time_drift"):  # pragma: no mutate - opaque rate-limit cache key text, never surfaced
            _LOGGER.debug("Error getting time drift: %s", type(err).__name__)  # pragma: no mutate - log message TEXT only
        return None


def get_active_rate_cost(updater, hass) -> Optional[float]:
    """Get active rate cost."""
    active_rate = _read_int(updater, "activeTarif")
    if active_rate is None:
        return None
    key = _RATE_COST_KEYS.get(active_rate)
    if not key:
        return None
    # Already bounded by the shared parse (raw hundredths), so a corrupt rate
    # is None rather than an absurd per-kWh price.
    value = updater.snapshot.get(key) if updater.available else None
    if value is None:
        return None
    return round(value / 100, 2)


def get_active_rate_attrs(updater, hass) -> dict:
    """Get active rate attributes."""
    if not updater.available:
        return {}
    active_rate = _read_int(updater, "activeTarif")
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

get_session_cost = _make_value_getter(
    "sessionMoney", precision=2)


# =============================================================================
# Adaptive charging (AI mode) and scheduled slots
# =============================================================================

get_adaptive_charging_state = _make_enum_getter(
    "aiStatus", {0: "Off", 1: "Voltage", 2: "Auto", 3: "Power"}
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


_rate_2_status = _make_rate_status_getter("tarifAEnable")
_rate_3_status = _make_rate_status_getter("tarifBEnable")
_schedule_1_state = _make_schedule_getter(1)
_schedule_2_state = _make_schedule_getter(2)


def _make_schedule_attrs(slot: int):
    """Slot details: window, optional current/energy caps.

    The caps are bounded by the shared parse — the amp value by THIS charger's
    design current, the energy value by the largest plausible slot cap — so an
    impossible figure is absent rather than shown. Firmware stores and reports
    sub-minimum setpoints verbatim (probe-verified, see the Number's
    read_min_value=0.0), so the floor is 0 A, not 7 A.
    """
    def getter(updater, hass) -> dict:
        if not updater.available:
            return {}
        snapshot = updater.snapshot
        start = _format_minutes(snapshot.get_int(f"sh{slot}Start"))
        stop = _format_minutes(snapshot.get_int(f"sh{slot}Stop"))
        attrs: Dict[str, Any] = {}
        if start and stop:
            attrs["window"] = f"{start}–{stop}"
            attrs["start"] = start
            attrs["stop"] = stop
        if snapshot.get_int(f"sh{slot}CurrentEnable") == 1:
            cur = snapshot.get_int(f"sh{slot}CurrentValue")
            if cur is not None:
                attrs["current_limit_a"] = cur
        if snapshot.get_int(f"sh{slot}EnergyEnable") == 1:
            energy = snapshot.get(f"sh{slot}EnergyValue")
            if energy is not None:
                attrs["energy_limit_kwh"] = energy
        return attrs
    return getter


# =============================================================================
# Connection quality
# =============================================================================

# Latency is published on a 0.5 s grid: finer steps are noise on a LAN poll,
# and the attribute is a diagnostic, not a measurement.
_LATENCY_STEP: Final[float] = 0.5
_LATENCY_ANCHOR: Final[str] = "__latency_avg"  # pragma: no mutate - equivalent: an opaque private key in the per-updater anchor store; any distinct value (None, a renamed literal) keys the same hold
_LATENCY_ANCHOR_SAMPLES: Final[str] = "__latency_avg_samples"  # pragma: no mutate - equivalent: an opaque private key in the per-updater anchor store; any distinct value (None, a renamed literal) keys the same hold


def _held_latency(updater, latency_avg: float, samples: object = None) -> float:
    """Snap latency to the display grid, holding the LAST PUBLISHED step.

    Rounding on its own is not a deadband. A rolling average parked on a step
    edge re-rounds to the other side on every poll, and an attribute change
    writes a recorder row exactly like a state change does — so the sensor
    churns while its state never moves. Measured on the live charger
    2026-09-12: the average sat on the 0.25 s edge, alternated 0.0 / 0.5, and
    was 25 of the 56 rows this integration wrote in a two-hour idle window —
    its single biggest writer with nothing charging. So the grid is only
    re-entered once the reading is a full step from what was last published.

    ``samples`` is how many response times the average is built from, and it
    exists because a hold is only as good as the value it latches onto. A cold
    start pays for connection setup, so the first samples run high; anchoring
    on them parks the figure on a spike it can never leave (the same charger
    reported 0.5 s for a whole run while answering in 0.10-0.13 s — a wrong
    reading held forever, which is worse than the churn the hold removes).
    While that count is still rising the window is still filling, so the figure
    tracks; once it stops rising the window is full and the hold takes over.
    A count that is absent or not an integer means an updater that does not
    report one, and holds from the first reading.

    The anchor lives on the updater (shared with ``_make_value_getter``'s
    deadbands), so two chargers never share one.
    """
    anchors = _deadband_anchor_store(updater)
    last = anchors.get(_LATENCY_ANCHOR)
    filling = (
        isinstance(samples, int)
        and not isinstance(samples, bool)
        and samples != anchors.get(_LATENCY_ANCHOR_SAMPLES)
    )
    if last is None or filling or abs(latency_avg - last) >= _LATENCY_STEP:
        last = round(latency_avg / _LATENCY_STEP) * _LATENCY_STEP
        anchors[_LATENCY_ANCHOR] = last
    anchors[_LATENCY_ANCHOR_SAMPLES] = samples
    return last


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
        if _should_log_error("get_connection_quality"):  # pragma: no mutate - opaque rate-limit cache key text, never surfaced
            _LOGGER.debug("Error getting connection quality: %s", type(err).__name__)  # pragma: no mutate - log message TEXT/exc_info; nothing asserts on either
        return None


def get_connection_attrs(updater, hass) -> dict:
    """Get connection attributes.

    Connection Quality measures HA→charger HTTP poll success.
    `wifi_rssi` is included as a supplementary metric (charger→AP link)
    because a degraded RSSI is the most common upstream cause of poor
    Connection Quality — surfacing both in one view makes diagnosis faster.
    """
    try:
        # No early-return when offline: success rate, latency, and status come
        # from the rolling poll metric (updated on failed polls too), so they
        # stay meaningful during an outage — that's when they matter most. Only
        # wifi_rssi is payload-derived and goes stale offline, so it is dropped.
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
            "latency_avg": _held_latency(
                updater, latency_avg, metrics.get("latency_samples")
            ),
            "status": status,
        }
        if updater.available:
            # The WiFi Signal sensor mirrors its own damped reading here
            # (`OptimizedEveusSensor._update_native_value`) on every poll, so
            # this reads the SAME value it publishes rather than damping RSSI
            # a second, independent time.
            rssi = getattr(updater, "_wifi_rssi_damped", None)
            if rssi is not None:
                attrs["wifi_rssi"] = rssi
        return attrs
    except Exception as err:
        if _should_log_error("get_connection_attrs"):  # pragma: no mutate - opaque rate-limit cache key text, never surfaced
            _LOGGER.debug("Error getting connection attributes: %s", type(err).__name__)  # pragma: no mutate - log message TEXT/exc_info; nothing asserts on either
        return {"status": "Error"}


# =============================================================================
# Sensor specification factory
# =============================================================================

def create_sensor_specifications(phases: int = 1) -> tuple[EveusSensorEntityDescription, ...]:
    """Create all sensor specifications using factory pattern.

    ``phases`` toggles per-phase voltage/current sensors for 3-phase chargers.

    The model maximum is no longer a parameter here: which charger this is
    belongs to the coordinator, so ``currentSet``, ``aiModecurrent`` and the
    schedule amp caps are bounded by it when the payload is parsed. A corrupt
    setpoint above the charger's capability (48 A reported by a 16 A unit)
    still reads ``unknown``; the specs no longer carry a second copy of the
    rule that decides it. The lower bound stays 0, NOT MIN_CURRENT — the
    firmware legitimately reports a setpoint below 7 A when one is configured
    directly on the charger, and that real value must be shown (HA writes are
    still floored at 7 A by the number).
    """

    current_set_getter = _make_value_getter("currentSet", precision=0)
    adaptive_current_getter = _make_value_getter("aiModecurrent", precision=0)

    # Measurement sensors
    measurements = [
        ("Voltage", get_voltage, ICON_FLASH, SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT, 0, None, 2),
        ("Current", get_current, ICON_CURRENT_AC, SensorDeviceClass.CURRENT, UnitOfElectricCurrent.AMPERE, 1, None, 0.2),
        ("Power", get_power, ICON_FLASH, SensorDeviceClass.POWER, UnitOfPower.WATT, 1, None, 50),
        (
            "Current Set",
            current_set_getter,
            ICON_CURRENT_AC,
            SensorDeviceClass.CURRENT,
            UnitOfElectricCurrent.AMPERE,
            0,
            EntityCategory.DIAGNOSTIC,
            None,
        ),
    ]

    measurement_specs = [
        EveusSensorEntityDescription(
            key=name.lower().replace(" ", "_"),
            name=name,
            value_fn=fn,
            icon=icon,
            device_class=device_class,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement=unit,
            suggested_display_precision=precision,
            entity_category=category,
            deadband=deadband,
        )
        for name, fn, icon, device_class, unit, precision, category, deadband in measurements
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
        EveusSensorEntityDescription(
            key=name.lower().replace(" ", "_"),
            name=name,
            value_fn=fn,
            icon=icon,
            device_class=device_class,
            state_class=state_class,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            suggested_display_precision=2,
        )
        for name, fn, icon, state_class, device_class in energy_sensors
    ]

    # Diagnostic sensors
    diagnostic_specs = [
        EveusSensorEntityDescription(
            key="state", name="State", value_fn=get_charger_state,
            attributes_fn=get_charger_state_attributes,
            icon="mdi:state-machine",
            entity_category=EntityCategory.DIAGNOSTIC,
            device_class=SensorDeviceClass.ENUM,
            options=list(CHARGING_STATES.values()) + ["Unknown"],
        ),
        EveusSensorEntityDescription(
            key="substate", name="Substate", value_fn=get_charger_substate,
            icon="mdi:information-variant",
            entity_category=EntityCategory.DIAGNOSTIC,
            device_class=SensorDeviceClass.ENUM,
            options=list(NORMAL_SUBSTATES.values())
            + [v for v in ERROR_STATES.values() if v != "No Error"]
            + ["Unknown State", "Unknown Error"],
        ),
        EveusSensorEntityDescription(
            key="not_charging_reason", name="Not Charging Reason",
            value_fn=get_not_charging_reason,
            attributes_fn=get_not_charging_reason_attrs,
            icon="mdi:help-circle-outline",
            entity_category=EntityCategory.DIAGNOSTIC,
            device_class=SensorDeviceClass.ENUM,
            options=list(NOT_CHARGING_REASON_OPTIONS),
        ),
        EveusSensorEntityDescription(
            key="ground", name="Ground", value_fn=get_ground_status,
            icon="mdi:electric-switch",
            entity_category=EntityCategory.DIAGNOSTIC,
            device_class=SensorDeviceClass.ENUM,
            options=list(get_ground_status.options),
        ),
        EveusSensorEntityDescription(
            key="time_drift", name="Time Drift", value_fn=get_time_drift,
            icon="mdi:clock-alert-outline",
            native_unit_of_measurement=UnitOfTime.SECONDS,
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        # Same tuple+comprehension idiom as `measurements` above: these six
        # differ only in name/getter/icon/device class/unit/precision, so
        # spelling out the three shared fields six times was pure repetition.
        *(
            EveusSensorEntityDescription(
                key=key,
                name=name,
                value_fn=fn,
                icon=icon,
                device_class=device_class,
                # Per-entry, not blanket: `state_class` is what turns on the
                # forever-kept 5-minute and hourly statistics, so only a
                # reading whose long-term trend is worth that cost declares it.
                state_class=state_class,
                native_unit_of_measurement=unit,
                suggested_display_precision=precision,
                entity_category=EntityCategory.DIAGNOSTIC,
                deadband=deadband,
            )
            for key, name, fn, icon, device_class, unit, precision, state_class, deadband in (
                ("box_temperature", "Box Temperature", get_box_temperature,
                 "mdi:thermometer", SensorDeviceClass.TEMPERATURE,
                 UnitOfTemperature.CELSIUS, 0, SensorStateClass.MEASUREMENT, 2),
                ("plug_temperature", "Plug Temperature", get_plug_temperature,
                 "mdi:thermometer-high", SensorDeviceClass.TEMPERATURE,
                 UnitOfTemperature.CELSIUS, 0, SensorStateClass.MEASUREMENT, 2),
                # The CR2032 clock cell drains over YEARS, and the recorder
                # keeps states for days — statistics is the only place that
                # slope exists, and it is what says to replace the cell before
                # the clock resets. Slow is not the same as static.
                ("battery_voltage", "Battery Voltage", get_battery_voltage,
                 "mdi:battery", SensorDeviceClass.VOLTAGE,
                 UnitOfElectricPotential.VOLT, 2, SensorStateClass.MEASUREMENT, None),
                # Leakage is an EVENT, not a trend: above the charger's 30 mA
                # threshold it trips and reports the fault itself, and
                # `leakValueH` is the charger's own peak-ever counter, so the
                # worst value stays readable from the device. Averaging a
                # healthy 0 mA every five minutes forever buys nothing.
                ("leak_current", "Leakage Current", get_leak_current,
                 "mdi:current-dc", SensorDeviceClass.CURRENT,
                 UnitOfElectricCurrent.MILLIAMPERE, 0, None, None),
                ("leak_current_peak", "Leakage Current Peak", get_leak_current_peak,
                 "mdi:current-dc", SensorDeviceClass.CURRENT,
                 UnitOfElectricCurrent.MILLIAMPERE, 0, None, None),
                # 5 dBm: measured on the live charger, RSSI wanders across ~7
                # dBm with the link unchanged — see get_wifi_rssi's comment.
                # Mirrored onto the updater for the Connection Quality
                # attribute (`get_connection_attrs`), so the single largest
                # source of recorder rows this integration produced is damped
                # once, not twice.
                ("wifi_signal", "WiFi Signal", get_wifi_rssi,
                 "mdi:wifi", SensorDeviceClass.SIGNAL_STRENGTH,
                 SIGNAL_STRENGTH_DECIBELS_MILLIWATT, 0, SensorStateClass.MEASUREMENT, 5),
            )
        ),
    ]

    if phases == 3:
        # Phases 2 and 3 repeat phase 1's metadata; current first, then voltage.
        # Same telemetry as phase 1, so the same damping — otherwise a 3-phase
        # entry keeps the per-poll churn the single-phase one just lost.
        for kind, getters, icon, device_class, unit, precision, deadband in (
            ("current", (get_current_phase_2, get_current_phase_3), ICON_CURRENT_AC,
             SensorDeviceClass.CURRENT, UnitOfElectricCurrent.AMPERE, 1, 0.2),
            ("voltage", (get_voltage_phase_2, get_voltage_phase_3), ICON_FLASH,
             SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT, 0, 2),
        ):
            for phase, getter in zip((2, 3), getters):
                diagnostic_specs.append(
                    EveusSensorEntityDescription(
                        key=f"{kind}_phase_{phase}", name=f"{kind.title()} Phase {phase}",
                        value_fn=getter,
                        icon=icon,
                        device_class=device_class,
                        state_class=SensorStateClass.MEASUREMENT,
                        native_unit_of_measurement=unit, suggested_display_precision=precision,
                        deadband=deadband,
                    )
                )

    # Special sensors
    special_specs = [
        EveusSensorEntityDescription(
            key="session_time", name="Session Time", value_fn=get_session_time,
            icon="mdi:timer",
            attributes_fn=get_session_time_attrs,
            restores_session_hold=True,
        ),
        EveusSensorEntityDescription(
            key="counter_a_cost", name="Counter A Cost", value_fn=get_counter_a_cost,
            icon=ICON_CURRENCY_UAH,
            device_class=SensorDeviceClass.MONETARY,
            state_class=SensorStateClass.TOTAL, native_unit_of_measurement=UNIT_UAH, suggested_display_precision=2,
            tracks_reset=True,
        ),
        EveusSensorEntityDescription(
            key="counter_b_cost", name="Counter B Cost", value_fn=get_counter_b_cost,
            icon=ICON_CURRENCY_UAH,
            device_class=SensorDeviceClass.MONETARY,
            state_class=SensorStateClass.TOTAL, native_unit_of_measurement=UNIT_UAH, suggested_display_precision=2,
            tracks_reset=True,
        ),
        EveusSensorEntityDescription(
            # The owner's own configured price (`tarif`/`tarif_2`/`tarif_3`),
            # not a measurement: no state_class, so it writes no statistics.
            key="primary_rate_cost", name="Primary Rate Cost", value_fn=get_primary_rate_cost,
            icon=ICON_CURRENCY_UAH,
            native_unit_of_measurement=UNIT_UAH_PER_KWH, suggested_display_precision=2,
        ),
        EveusSensorEntityDescription(
            key="active_rate_cost", name="Active Rate Cost", value_fn=get_active_rate_cost,
            icon=ICON_CURRENCY_UAH,
            state_class=SensorStateClass.MEASUREMENT, native_unit_of_measurement=UNIT_UAH_PER_KWH, suggested_display_precision=2,
            attributes_fn=get_active_rate_attrs,
        ),
        EveusSensorEntityDescription(
            # The owner's own configured price (`tarif`/`tarif_2`/`tarif_3`),
            # not a measurement: no state_class, so it writes no statistics.
            key="rate_2_cost", name="Rate 2 Cost", value_fn=get_rate2_cost,
            icon=ICON_CURRENCY_UAH,
            native_unit_of_measurement=UNIT_UAH_PER_KWH, suggested_display_precision=2,
        ),
        EveusSensorEntityDescription(
            # The owner's own configured price (`tarif`/`tarif_2`/`tarif_3`),
            # not a measurement: no state_class, so it writes no statistics.
            key="rate_3_cost", name="Rate 3 Cost", value_fn=get_rate3_cost,
            icon=ICON_CURRENCY_UAH,
            native_unit_of_measurement=UNIT_UAH_PER_KWH, suggested_display_precision=2,
        ),
        EveusSensorEntityDescription(
            key="rate_2_status", name="Rate 2 Status",
            value_fn=_rate_2_status,
            icon="mdi:clock-check",
            device_class=SensorDeviceClass.ENUM,
            options=list(_rate_2_status.options),
        ),
        EveusSensorEntityDescription(
            key="rate_3_status", name="Rate 3 Status",
            value_fn=_rate_3_status,
            icon="mdi:clock-check",
            device_class=SensorDeviceClass.ENUM,
            options=list(_rate_3_status.options),
        ),
        EveusSensorEntityDescription(
            key="session_cost", name="Session Cost", value_fn=get_session_cost,
            icon="mdi:cash",
            device_class=SensorDeviceClass.MONETARY,
            state_class=SensorStateClass.TOTAL, native_unit_of_measurement=UNIT_UAH, suggested_display_precision=2,
            tracks_reset=True,
        ),
        EveusSensorEntityDescription(
            key="adaptive_charging", name="Adaptive Charging",
            value_fn=get_adaptive_charging_state,
            icon="mdi:auto-mode",
            entity_category=EntityCategory.DIAGNOSTIC,
            device_class=SensorDeviceClass.ENUM,
            options=list(get_adaptive_charging_state.options),
        ),
        EveusSensorEntityDescription(
            key="adaptive_current_limit", name="Adaptive Current Limit",
            value_fn=adaptive_current_getter,
            icon=ICON_CURRENT_AC,
            device_class=SensorDeviceClass.CURRENT,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, suggested_display_precision=0,
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        EveusSensorEntityDescription(
            key="schedule_1", name="Schedule 1",
            value_fn=_schedule_1_state,
            attributes_fn=_make_schedule_attrs(1),
            icon="mdi:calendar-clock",
            entity_category=EntityCategory.DIAGNOSTIC,
            device_class=SensorDeviceClass.ENUM,
            options=list(_schedule_1_state.options),
        ),
        EveusSensorEntityDescription(
            key="schedule_2", name="Schedule 2",
            value_fn=_schedule_2_state,
            attributes_fn=_make_schedule_attrs(2),
            icon="mdi:calendar-clock",
            entity_category=EntityCategory.DIAGNOSTIC,
            device_class=SensorDeviceClass.ENUM,
            options=list(_schedule_2_state.options),
        ),
        EveusSensorEntityDescription(
            key="connection_quality", name="Connection Quality",
            available_when_offline=True,
            value_fn=get_connection_quality,
            icon="mdi:connection",
            state_class=SensorStateClass.MEASUREMENT, native_unit_of_measurement=PERCENTAGE, suggested_display_precision=0,
            entity_category=EntityCategory.DIAGNOSTIC, attributes_fn=get_connection_attrs,
        ),
    ]

    result = tuple(measurement_specs + energy_specs + diagnostic_specs + special_specs)
    keys = [s.key for s in result]
    if len(keys) != len(set(keys)):
        duplicates = sorted({k for k in keys if keys.count(k) > 1})
        raise RuntimeError(f"duplicate sensor keys: {duplicates}")
    return result


@lru_cache(maxsize=8)
def get_sensor_specifications(phases: int = 1) -> tuple[EveusSensorEntityDescription, ...]:
    """Get sensor specifications for the given phase count (cached)."""
    return create_sensor_specifications(phases=phases)
