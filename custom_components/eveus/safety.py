"""Safety Repairs notices for Eveus chargers.

Combines authoritative firmware fault codes (charger ``state=7`` plus its
``subState`` code) with conservative, debounced raw telemetry so Home Assistant
can warn about dangerous grounding, leakage, overheat, and other charger faults
without false alarms. This module performs no I/O and sends no charger commands;
it only reports conditions through the issue registry.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping

from homeassistant.helpers import issue_registry as ir

from .const import (
    CHARGING_STATES,
    DEVICE_STATE_ERROR,
    DOMAIN,
    ERROR_STATES,
    FAULT_RECOVERY_POLLS,
    GROUND_CLEAR_POLLS,
    GROUND_CONTROL_CLEAR_POLLS,
    GROUND_CONTROL_TRIGGER_POLLS,
    GROUND_TRIGGER_POLLS,
    LEAKAGE_HIGH_MA,
    LEAKAGE_RECOVERED_MA,
    LEAKAGE_RECOVERY_POLLS,
    LEAKAGE_TRIGGER_POLLS,
    MAX_VALID_LEAKAGE_CURRENT_MA,
    MAX_VALID_TEMPERATURE_C,
    MIN_VALID_TEMPERATURE_C,
    TEMPERATURE_HIGH_C,
    TEMPERATURE_RECOVERED_C,
    TEMPERATURE_RECOVERY_POLLS,
    TEMPERATURE_TRIGGER_POLLS,
)
from .utils import get_safe_value

# A raw-telemetry signal returns True (condition present), False (absent), or
# None (unknown — missing/corrupt/out-of-domain data that must not move any
# trigger or recovery counter).
Signal = Callable[[Mapping[str, Any]], "bool | None"]


class SafetyLifecycle(Enum):
    """How a recovered safety issue is removed."""

    AUTO_CLEAR = "auto_clear"
    LATCHED = "latched"


@dataclass(frozen=True, slots=True)
class SafetyPolicy:
    """One user-facing safety issue policy."""

    key: str
    fault_codes: frozenset[int]
    lifecycle: SafetyLifecycle
    raw_trigger: Signal | None = None
    raw_recovered: Signal | None = None
    trigger_polls: int = 1
    recovery_polls: int = FAULT_RECOVERY_POLLS


def safety_issue_id(entry, key: str) -> str:
    """Return an entry-scoped safety issue ID."""
    return f"safety_{key}_{entry.entry_id}"


def _policy(
    key: str,
    *fault_codes: int,
    lifecycle: SafetyLifecycle = SafetyLifecycle.LATCHED,
    raw_trigger: Signal | None = None,
    raw_recovered: Signal | None = None,
    trigger_polls: int = 1,
    recovery_polls: int = FAULT_RECOVERY_POLLS,
) -> SafetyPolicy:
    return SafetyPolicy(
        key=key,
        fault_codes=frozenset(fault_codes),
        lifecycle=lifecycle,
        raw_trigger=raw_trigger,
        raw_recovered=raw_recovered,
        trigger_polls=trigger_polls,
        recovery_polls=recovery_polls,
    )


# Sentinel distinct from ``None``: ``None`` means "no firmware fault", whereas
# ``_UNKNOWN`` means the fault state itself could not be read and must not move
# any counter.
_UNKNOWN = object()


def _fault_code(data: Mapping[str, Any]) -> int | None | object:
    """Return the firmware fault code, ``None`` (no fault), or ``_UNKNOWN``.

    ``_UNKNOWN`` covers a missing/out-of-domain ``state`` or, while in the error
    state, a missing/out-of-domain ``subState`` — neither may advance or reset a
    trigger or recovery streak.
    """
    state = get_safe_value(data, "state", int)
    if state not in CHARGING_STATES:
        return _UNKNOWN
    if state != DEVICE_STATE_ERROR:
        return None
    substate = get_safe_value(data, "subState", int)
    return substate if substate in ERROR_STATES and substate != 0 else _UNKNOWN


def _equals(key: str, expected: int, allowed: frozenset[int]) -> Signal:
    """Tri-state equality on an enum field; unknown when value is out of domain."""

    def evaluate(data: Mapping[str, Any]) -> bool | None:
        value = get_safe_value(data, key, int)
        if value not in allowed:
            return None
        return value == expected

    return evaluate


def _bounded_float(
    key: str, *, minimum: float, maximum: float
) -> Callable[[Mapping[str, Any]], float | None]:
    """Return a finite reading within physical bounds, else ``None`` (unknown).

    A finite but impossible outlier (e.g. ``temperature1=1e9``) is unknown, not
    an event — the same physical-sanity bounds the display sensors apply.
    """

    def evaluate(data: Mapping[str, Any]) -> float | None:
        value = get_safe_value(data, key, float)
        if value is None or not minimum <= value <= maximum:
            return None
        return value

    return evaluate


def _at_least(key: str, threshold: float, *, minimum: float, maximum: float) -> Signal:
    value_of = _bounded_float(key, minimum=minimum, maximum=maximum)

    def evaluate(data: Mapping[str, Any]) -> bool | None:
        value = value_of(data)
        return None if value is None else value >= threshold

    return evaluate


def _at_most(key: str, threshold: float, *, minimum: float, maximum: float) -> Signal:
    value_of = _bounded_float(key, minimum=minimum, maximum=maximum)

    def evaluate(data: Mapping[str, Any]) -> bool | None:
        value = value_of(data)
        return None if value is None else value <= threshold

    return evaluate


def _below(key: str, threshold: float, *, minimum: float, maximum: float) -> Signal:
    value_of = _bounded_float(key, minimum=minimum, maximum=maximum)

    def evaluate(data: Mapping[str, Any]) -> bool | None:
        value = value_of(data)
        return None if value is None else value < threshold

    return evaluate


_GROUND_DOMAIN = frozenset({0, 1})


POLICIES: tuple[SafetyPolicy, ...] = (
    _policy(
        "ground_missing",
        1,
        lifecycle=SafetyLifecycle.AUTO_CLEAR,
        raw_trigger=_equals("ground", 0, _GROUND_DOMAIN),
        raw_recovered=_equals("ground", 1, _GROUND_DOMAIN),
        trigger_polls=GROUND_TRIGGER_POLLS,
        recovery_polls=GROUND_CLEAR_POLLS,
    ),
    _policy(
        "ground_control_disabled",
        lifecycle=SafetyLifecycle.AUTO_CLEAR,
        raw_trigger=_equals("groundCtrl", 0, _GROUND_DOMAIN),
        raw_recovered=_equals("groundCtrl", 1, _GROUND_DOMAIN),
        trigger_polls=GROUND_CONTROL_TRIGGER_POLLS,
        recovery_polls=GROUND_CONTROL_CLEAR_POLLS,
    ),
    _policy(
        "leakage_detected",
        2,
        4,
        raw_trigger=_at_least(
            "leakValue",
            LEAKAGE_HIGH_MA,
            minimum=0,
            maximum=MAX_VALID_LEAKAGE_CURRENT_MA,
        ),
        raw_recovered=_below(
            "leakValue",
            LEAKAGE_RECOVERED_MA,
            minimum=0,
            maximum=MAX_VALID_LEAKAGE_CURRENT_MA,
        ),
        trigger_polls=LEAKAGE_TRIGGER_POLLS,
        recovery_polls=LEAKAGE_RECOVERY_POLLS,
    ),
    _policy("relay_fault", 3),
    _policy(
        "box_overheat",
        5,
        raw_trigger=_at_least(
            "temperature1",
            TEMPERATURE_HIGH_C,
            minimum=MIN_VALID_TEMPERATURE_C,
            maximum=MAX_VALID_TEMPERATURE_C,
        ),
        raw_recovered=_at_most(
            "temperature1",
            TEMPERATURE_RECOVERED_C,
            minimum=MIN_VALID_TEMPERATURE_C,
            maximum=MAX_VALID_TEMPERATURE_C,
        ),
        trigger_polls=TEMPERATURE_TRIGGER_POLLS,
        recovery_polls=TEMPERATURE_RECOVERY_POLLS,
    ),
    _policy(
        "plug_overheat",
        6,
        raw_trigger=_at_least(
            "temperature2",
            TEMPERATURE_HIGH_C,
            minimum=MIN_VALID_TEMPERATURE_C,
            maximum=MAX_VALID_TEMPERATURE_C,
        ),
        raw_recovered=_at_most(
            "temperature2",
            TEMPERATURE_RECOVERED_C,
            minimum=MIN_VALID_TEMPERATURE_C,
            maximum=MAX_VALID_TEMPERATURE_C,
        ),
        trigger_polls=TEMPERATURE_TRIGGER_POLLS,
        recovery_polls=TEMPERATURE_RECOVERY_POLLS,
    ),
    _policy("pilot_fault", 7),
    _policy("low_voltage", 8),
    _policy("diode_fault", 9),
    _policy("overcurrent", 10),
    _policy("interface_timeout", 11),
    _policy("software_failure", 12),
    _policy("gfci_test_failure", 13),
    _policy("high_voltage", 14),
)


def evaluate_policy_signals(
    policy: SafetyPolicy, data: Mapping[str, Any]
) -> tuple[bool | None, bool | None]:
    """Return ``(trigger, recovered)`` tri-state signals for one policy.

    An authoritative firmware fault wins immediately. Otherwise the raw signal
    decides. ``None`` (unknown) is preserved end-to-end so the caller never
    advances or resets a streak from missing/corrupt data. A policy with no raw
    trigger cannot fire from raw telemetry (``raw_trigger`` defaults to absent);
    one with no raw recovery treats raw as already recovered so recovery depends
    only on the firmware fault clearing.
    """
    fault = _fault_code(data)
    if fault is _UNKNOWN:
        fault_matches: bool | None = None
        fault_recovered: bool | None = None
    else:
        fault_matches = fault in policy.fault_codes
        fault_recovered = not fault_matches

    raw_trigger = policy.raw_trigger(data) if policy.raw_trigger else False
    raw_recovered = policy.raw_recovered(data) if policy.raw_recovered else True

    if fault_matches is True or raw_trigger is True:
        trigger: bool | None = True
    elif policy.raw_trigger is not None and raw_trigger is None:
        trigger = None
    elif policy.raw_trigger is None and fault_matches is None:
        trigger = None
    else:
        trigger = False

    if fault_recovered is False or raw_recovered is False:
        recovered: bool | None = False
    elif fault_recovered is None or raw_recovered is None:
        recovered = None
    else:
        recovered = True

    return trigger, recovered


def matching_firmware_fault(policy: SafetyPolicy, data: Mapping[str, Any]) -> bool:
    """Return whether the current payload carries this policy's firmware fault."""
    fault = _fault_code(data)
    return isinstance(fault, int) and fault in policy.fault_codes


def _create_issue(hass, entry, policy: SafetyPolicy) -> None:
    """Create one persistent, non-fixable safety issue."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        safety_issue_id(entry, policy.key),
        is_fixable=False,
        is_persistent=True,
        issue_domain=DOMAIN,
        severity=(
            ir.IssueSeverity.WARNING
            if policy.key == "ground_control_disabled"
            else ir.IssueSeverity.ERROR
        ),
        translation_key=f"safety_{policy.key}",
    )


@dataclass(slots=True)
class SafetyPolicyState:
    """Mutable per-policy debounce/recovery counters."""

    trigger_streak: int = 0
    recovery_streak: int = 0
    recovered: bool = False


class EveusSafetyManager:
    """Evaluate and reconcile safety issues for one config entry.

    Registered as a coordinator listener: each successful poll runs ``process``,
    which holds debounce, recovery hysteresis, latching, and ignored-issue
    handling. It performs no I/O and sends no charger commands.
    """

    def __init__(self, hass, entry, updater) -> None:
        self._hass = hass
        self._entry = entry
        self._updater = updater
        self._states = {policy.key: SafetyPolicyState() for policy in POLICIES}

    def process(self) -> None:
        """Reconcile every policy against the latest successful payload.

        A failed or unavailable poll is skipped entirely so stale cached data is
        never replayed into a debounce or recovery streak.
        """
        if (
            not self._updater.available
            or not self._updater.last_update_success
            or not isinstance(self._updater.data, dict)
        ):
            return
        data = self._updater.data
        for policy in POLICIES:
            self._process_policy(policy, self._states[policy.key], data)

    def _process_policy(
        self,
        policy: SafetyPolicy,
        state: SafetyPolicyState,
        data: Mapping[str, Any],
    ) -> None:
        """Reconcile one policy against one fresh successful payload."""
        trigger, recovered = evaluate_policy_signals(policy, data)
        issue_id = safety_issue_id(self._entry, policy.key)
        issue = ir.async_get(self._hass).async_get_issue(DOMAIN, issue_id)

        if trigger is True:
            state.recovery_streak = 0
            state.recovered = False
            if matching_firmware_fault(policy, data):
                # An authoritative fault bypasses raw debounce entirely.
                state.trigger_streak = policy.trigger_polls
            else:
                state.trigger_streak = min(
                    state.trigger_streak + 1, policy.trigger_polls
                )
            if state.trigger_streak >= policy.trigger_polls and issue is None:
                _create_issue(self._hass, self._entry, policy)
            return

        # trigger is False -> condition absent; None -> unknown, leave streak.
        if trigger is False:
            state.trigger_streak = 0

        if issue is None:
            state.recovery_streak = 0
            state.recovered = False
            return

        if recovered is True:
            state.recovery_streak = min(
                state.recovery_streak + 1, policy.recovery_polls
            )
            state.recovered = state.recovery_streak >= policy.recovery_polls
        elif recovered is False:
            state.recovery_streak = 0
            state.recovered = False
        # recovered is None -> unknown: leave recovery streak untouched.

        if not state.recovered:
            return

        # Auto-clear issues delete on confirmed recovery. Latched issues persist
        # until the user has also pressed Ignore (dismissed_version set);
        # deleting then clears HA's stored dismissal so a future separate
        # incident with the same ID can alert again.
        if (
            policy.lifecycle is SafetyLifecycle.AUTO_CLEAR
            or issue.dismissed_version is not None
        ):
            ir.async_delete_issue(self._hass, DOMAIN, issue_id)
            state.trigger_streak = 0
            state.recovery_streak = 0
            state.recovered = False
