"""One typed parse of an Eveus ``/main`` payload.

The charger answers every poll with a flat JSON dict of ~80 fields. Before this
module each consumer converted and range-checked the fields it cared about on
its own, so the same physical ceiling was spelled out in the sensors, the safety
policies, the SOC limit and the coordinator — and two consumers of one field
could (and did) disagree about when a reading was usable.

``EveusSnapshot.parse`` applies the conversion and the bounds exactly once, from
the single ``_FIELDS`` table below. ``None`` means "absent or unusable"
everywhere, so no consumer re-checks a physical bound. What stays with the
consumer is policy that is genuinely its own: an entity's writable range, a
display deadband, a debounce streak, an enum domain.

The raw payload is kept verbatim for diagnostics, the dashboard card and the
device-registry strings. Firmware-1.x state translation happens in the
coordinator, which owns the latch, and runs BEFORE this parse — so ``state``
here is always in the modern 0-7 domain when the charger speaks it at all.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Final

from .const import (
    BATTERY_VBAT_MAX_PLAUSIBLE_VOLTS,
    CHARGING_STATES,
    CONNECTED_STATES,
    MAX_COST_VALUE,
    MAX_CURRENT_A,
    MAX_ENERGY_KWH,
    MAX_POWER_W,
    MAX_RATE_HUNDREDTHS,
    MAX_SCHEDULE_ENERGY_KWH,
    MAX_SESSION_TIME_SECONDS,
    MAX_VALID_LEAKAGE_CURRENT_MA,
    MAX_VALID_RSSI_DBM,
    MAX_VALID_SYSTEM_TIME,
    MAX_VALID_TEMPERATURE_C,
    MAX_VALID_TIMEZONE_H,
    MAX_VOLTAGE_V,
    MIN_VALID_RSSI_DBM,
    MIN_VALID_TEMPERATURE_C,
    MIN_VALID_TIMEZONE_H,
    MODEL_MAX_CURRENT,
    PLUG_UNKNOWN_STATES,
    SESSION_ACTIVE_STATES,
    is_modern_firmware_payload,
)
from .utils import get_safe_value

# Sentinel for a ceiling that depends on the configured charger model rather
# than on physics: an amp setpoint above THIS charger's design current is a
# wrong-device or corrupt payload, and 48 A is fine on a 48 A unit.
MODEL_MAX: Final = object()

_MAX_MODEL_CURRENT: Final[int] = max(MODEL_MAX_CURRENT.values())


@dataclass(frozen=True, slots=True)
class _Field:
    """How one payload key is converted and bounded, once."""

    converter: Callable[[Any], Any]
    minimum: float | None = None
    maximum: float | Any | None = None
    # ``vBat`` is the only field where the floor itself is impossible: a coin
    # cell reading exactly 0 V is a dropped ADC sample, not a flat battery.
    exclusive_min: bool = False


def _f(minimum=None, maximum=None, *, exclusive_min: bool = False) -> _Field:
    return _Field(float, minimum, maximum, exclusive_min)


def _i(minimum=None, maximum=None) -> _Field:
    return _Field(int, minimum, maximum)


# The one table. A key absent from it cannot be read through a snapshot at all
# (``get`` raises), so a typo is loud instead of reading as `unknown` forever.
_FIELDS: Final[dict[str, _Field]] = {
    # --- device state -------------------------------------------------------
    # 0-255 mirrors validate_main_payload: the charger's state byte is
    # inherently a byte, and firmware 1.x uses codes outside the 0-7 map.
    "state": _i(0, 255),
    "subState": _i(),
    # --- electrical measurements -------------------------------------------
    "curMeas1": _f(0, MAX_CURRENT_A),
    "curMeas2": _f(0, MAX_CURRENT_A),
    "curMeas3": _f(0, MAX_CURRENT_A),
    "voltMeas1": _f(0, MAX_VOLTAGE_V),
    "voltMeas2": _f(0, MAX_VOLTAGE_V),
    "voltMeas3": _f(0, MAX_VOLTAGE_V),
    "powerMeas": _f(0, MAX_POWER_W),
    # --- energy and money counters -----------------------------------------
    "sessionEnergy": _f(0, MAX_ENERGY_KWH),
    "totalEnergy": _f(0, MAX_ENERGY_KWH),
    "IEM1": _f(0, MAX_ENERGY_KWH),
    "IEM2": _f(0, MAX_ENERGY_KWH),
    "sessionMoney": _f(0, MAX_COST_VALUE),
    "IEM1_money": _f(0, MAX_COST_VALUE),
    "IEM2_money": _f(0, MAX_COST_VALUE),
    "sessionTime": _i(0, MAX_SESSION_TIME_SECONDS),
    # --- tariffs ------------------------------------------------------------
    "activeTarif": _i(),
    "tarif": _f(0, MAX_RATE_HUNDREDTHS),
    "tarifAValue": _f(0, MAX_RATE_HUNDREDTHS),
    "tarifBValue": _f(0, MAX_RATE_HUNDREDTHS),
    "tarifAEnable": _i(),
    "tarifBEnable": _i(),
    # Rate 2/3 windows, charger-local minutes of day.
    "tarifAStart": _i(0, 1439),
    "tarifAStop": _i(0, 1439),
    "tarifBStart": _i(0, 1439),
    "tarifBStop": _i(0, 1439),
    # --- diagnostics --------------------------------------------------------
    "temperature1": _f(MIN_VALID_TEMPERATURE_C, MAX_VALID_TEMPERATURE_C),
    "temperature2": _f(MIN_VALID_TEMPERATURE_C, MAX_VALID_TEMPERATURE_C),
    "vBat": _f(0, BATTERY_VBAT_MAX_PLAUSIBLE_VOLTS, exclusive_min=True),
    "leakValue": _f(0, MAX_VALID_LEAKAGE_CURRENT_MA),
    "leakValueH": _f(0, MAX_VALID_LEAKAGE_CURRENT_MA),
    "RSSI": _f(MIN_VALID_RSSI_DBM, MAX_VALID_RSSI_DBM),
    "ground": _i(),
    "groundCtrl": _i(),
    # The charger's RTC, encoded as epoch seconds shifted by `timeZone`. A
    # zero or negative stamp is an unset/garbled clock, not midnight 1970.
    "systemTime": _Field(int, 0, MAX_VALID_SYSTEM_TIME, exclusive_min=True),
    "timeZone": _i(MIN_VALID_TIMEZONE_H, MAX_VALID_TIMEZONE_H),
    # --- setpoints and flags the controls write -----------------------------
    "currentSet": _f(0, MODEL_MAX),
    "evseEnabled": _i(),
    "oneCharge": _i(),
    "suspendLimits": _i(),
    "suspendErrors": _i(),
    "ocppEnabled": _i(),
    "ocppconnected": _i(),
    "timeLimit": _f(),
    "timeLimitS": _i(),
    "energyLimit": _f(),
    "energyLimitS": _i(),
    "moneyLimit": _f(),
    "moneyLimitS": _i(),
    "minVoltage": _f(),
    "aiStatus": _i(),
    "aiMode": _i(),
    "aiVoltage": _f(),
    "aiModecurrent": _f(0, MODEL_MAX),
    # --- schedule slots -----------------------------------------------------
    "sh1Enabled": _i(),
    "sh2Enabled": _i(),
    "sh1Start": _i(),
    "sh1Stop": _i(),
    "sh2Start": _i(),
    "sh2Stop": _i(),
    "sh1CurrentEnable": _i(),
    "sh2CurrentEnable": _i(),
    "sh1EnergyEnable": _i(),
    "sh2EnergyEnable": _i(),
    "sh1CurrentValue": _f(0, MODEL_MAX),
    "sh2CurrentValue": _f(0, MODEL_MAX),
    "sh1EnergyValue": _f(0, MAX_SCHEDULE_ENERGY_KWH),
    "sh2EnergyValue": _f(0, MAX_SCHEDULE_ENERGY_KWH),
}


def model_max_current(model: str | None) -> int:
    """This charger's design current, falling back to the largest model.

    Mirrors ``_payload.validate_main_payload``: without a configured model a
    corrupt setpoint is still bounded by the biggest charger we support, so it
    cannot pass as a healthy reading.
    """
    return MODEL_MAX_CURRENT.get(model) or _MAX_MODEL_CURRENT


def _parse_fields(payload: Mapping[str, Any], max_current: int) -> dict[str, Any]:
    """Convert and bound every known key, dropping anything unusable."""
    values: dict[str, Any] = {}
    for key, field in _FIELDS.items():
        if key not in payload:
            continue
        # get_safe_value owns the conversion rules (bools, non-finite floats,
        # HA's `unknown`/`unavailable` strings, fractional floats in an int
        # field). Bounds are applied here, on top of the converted value.
        value = get_safe_value(payload, key, field.converter)
        if value is None:
            continue
        minimum = field.minimum
        if minimum is not None and (
            value <= minimum if field.exclusive_min else value < minimum
        ):
            continue
        maximum = max_current if field.maximum is MODEL_MAX else field.maximum
        if maximum is not None and value > maximum:
            continue
        values[key] = value
    return values


@dataclass(frozen=True, slots=True)
class EveusSnapshot:
    """One parsed ``/main`` payload: converted once, bounded once."""

    raw: Mapping[str, Any]
    values: Mapping[str, Any]
    model: str | None = None

    @classmethod
    def parse(
        cls, payload: Mapping[str, Any] | Any, model: str | None = None
    ) -> "EveusSnapshot":
        """Build a snapshot from a validated ``/main`` payload."""
        if not isinstance(payload, Mapping):
            # Never raised in production (the payload validator runs first),
            # but a snapshot must not be the thing that breaks a poll.
            return EMPTY_SNAPSHOT
        return cls(
            raw=payload,
            values=_parse_fields(payload, model_max_current(model)),
            model=model,
        )

    @classmethod
    def empty(cls) -> "EveusSnapshot":
        """The snapshot in force before the charger has ever answered."""
        return EMPTY_SNAPSHOT

    # --- accessors ----------------------------------------------------------

    def get(self, key: str) -> Any:
        """The converted, bounded value for a known key, or ``None``."""
        if key not in _FIELDS:
            raise KeyError(key)
        return self.values.get(key)

    def get_int(self, key: str) -> int | None:
        """A known key as a whole number, or ``None`` when it is not one.

        Some fields are read as floats by one consumer (a number entity's
        setpoint) and as whole numbers by another (the schedule attributes).
        The parse is shared; only the integrality question is asked here, and
        it is asked the same way ``get_safe_value(..., int)`` asks it — a
        fractional reading is corrupt, not something to truncate.
        """
        value = self.get(key)
        if value is None or isinstance(value, int):
            return value
        return int(value) if float(value).is_integer() else None

    def has(self, key: str) -> bool:
        """Whether the charger reported this key at all, usable or not.

        The controls distinguish "the charger stopped sending this field" from
        "the charger sent something we cannot use"; only the first releases a
        pinned optimistic write.
        """
        return key in self.raw

    # --- typed views --------------------------------------------------------

    @property
    def state(self) -> int | None:
        """Device state code, already legacy-normalised by the coordinator."""
        return self.values.get("state")

    @property
    def substate(self) -> int | None:
        return self.values.get("subState")

    @property
    def current_set(self) -> float | None:
        return self.values.get("currentSet")

    @property
    def cur_meas(self) -> tuple[float | None, float | None, float | None]:
        return (
            self.values.get("curMeas1"),
            self.values.get("curMeas2"),
            self.values.get("curMeas3"),
        )

    @property
    def volt_meas(self) -> tuple[float | None, float | None, float | None]:
        return (
            self.values.get("voltMeas1"),
            self.values.get("voltMeas2"),
            self.values.get("voltMeas3"),
        )

    @property
    def power_w(self) -> float | None:
        return self.values.get("powerMeas")

    @property
    def session_energy_kwh(self) -> float | None:
        return self.values.get("sessionEnergy")

    @property
    def session_time_s(self) -> int | None:
        return self.values.get("sessionTime")

    @property
    def session_money(self) -> float | None:
        return self.values.get("sessionMoney")

    @property
    def charger_wall_clock_s(self) -> int | None:
        """The charger's LOCAL wall clock in epoch-style seconds.

        ``systemTime`` is stored UTC shifted by the ``timeZone`` select, and the
        wall clock — not the decoded UTC — is what schedules and tariff windows
        run on. So a drift check compares it against Home Assistant's local wall
        clock; comparing UTC to UTC cancels ``timeZone`` out and goes blind to a
        wrong timezone or a DST mismatch.

        ``None`` unless BOTH fields are present and inside their sanity windows
        (the snapshot applied those), so the Time Drift sensor and the
        clock-drift notice cannot read the clock two different ways.
        """
        if self.values.get("timeZone") is None:
            return None
        return self.values.get("systemTime")

    @property
    def known_state(self) -> int | None:
        """The state code, but only when the modern 0-7 map can name it.

        Firmware may report a code we have no meaning for (1.x uses 20). Every
        consumer that answers a question about what the charger is doing has to
        stand down there rather than guess, so the check lives here once.
        """
        state = self.values.get("state")
        return state if state in CHARGING_STATES else None

    @property
    def session_active(self) -> bool | None:
        """Whether a charging session is running; ``None`` when indeterminate.

        In the Error state the firmware cannot tell, and a definite "no" there
        would falsely trigger session-ended automations.
        """
        return self._plug_view(SESSION_ACTIVE_STATES)

    @property
    def plugged_in(self) -> bool | None:
        """Whether a car is physically connected; ``None`` in the Error state."""
        return self._plug_view(CONNECTED_STATES)

    def _plug_view(self, states: frozenset[int]) -> bool | None:
        state = self.known_state
        if state is None or state in PLUG_UNKNOWN_STATES:
            return None
        return state in states

    @property
    def modern_firmware(self) -> bool:
        """Whether this payload carries the modern firmware marker.

        The coordinator's sticky verdict is the authority (one degraded reply
        must not demote a modern charger); this is the per-payload fact it is
        built from.
        """
        return is_modern_firmware_payload(self.raw)


EMPTY_SNAPSHOT: Final[EveusSnapshot] = EveusSnapshot(raw={}, values={}, model=None)
