"""Why the charger is not charging — one answer for the sensor and the finish event.

The Not Charging Reason sensor and the ``eveus_charging_finished`` event both
answer "what stopped this charge". Kept in one place so the two can never name
the same stop differently.
"""
from __future__ import annotations

from typing import Callable, Dict, Final, Optional

from .const import (
    CHARGING_STATES,
    DEVICE_STATE_CHARGING,
    DEVICE_STATE_COMPLETE,
    DEVICE_STATE_ERROR,
    DEVICE_STATE_STANDBY,
    FINISHED_REASONS,
)

# The two reasons both schedule slots share — named so schedule 1 and 2 cannot
# drift into two differently-spelled versions of the same reason.
_REASON_WAITING_FOR_SCHEDULE: Final[str] = "Waiting for Schedule"
_REASON_SCHEDULE_ENERGY_LIMIT: Final[str] = "Schedule Energy Limit Reached"
_REASON_CHARGE_COMPLETE: Final[str] = "Charge Complete"
_REASON_WAITING_FOR_CAR: Final[str] = "Waiting for Car"
_REASON_STOPPED_BY_USER: Final[str] = "Stopped by User"
_REASON_ENERGY_LIMIT: Final[str] = "Energy Limit Reached"
_REASON_TIME_LIMIT: Final[str] = "Time Limit Reached"
_REASON_COST_LIMIT: Final[str] = "Cost Limit Reached"

NOT_CHARGING_REASON_OPTIONS: Final[tuple[str, ...]] = (
    "Charging",
    "Starting Up",
    "Cable Not Connected",
    _REASON_WAITING_FOR_CAR,
    _REASON_CHARGE_COMPLETE,
    _REASON_STOPPED_BY_USER,
    _REASON_ENERGY_LIMIT,
    _REASON_TIME_LIMIT,
    _REASON_COST_LIMIT,
    _REASON_WAITING_FOR_SCHEDULE,
    _REASON_SCHEDULE_ENERGY_LIMIT,
    "Waiting for Activation",
    "Paused by Adaptive Mode",
    "Paused",
    "Controlled by OCPP",
    "Error",
    "Unknown",
)

# subState meaning while the charger holds off rather than delivering current.
# Schedule 1 and 2 collapse to one reason: which schedule fired is in the
# Schedule sensors.
SUBSTATE_REASONS: Final[Dict[int, str]] = {
    1: _REASON_STOPPED_BY_USER,
    2: _REASON_ENERGY_LIMIT,
    3: _REASON_TIME_LIMIT,
    4: _REASON_COST_LIMIT,
    5: _REASON_WAITING_FOR_SCHEDULE,
    6: _REASON_SCHEDULE_ENERGY_LIMIT,
    7: _REASON_WAITING_FOR_SCHEDULE,
    8: _REASON_SCHEDULE_ENERGY_LIMIT,
    9: "Waiting for Activation",
    10: "Paused by Adaptive Mode",
}

_SUBSTATE_ACTIVATION: Final[int] = 9

# The finish-event reason for a stop the charger made on its own settings. Any
# other reason keeps the state-derived FINISHED_REASONS value it always had.
SUBSTATE_FINISH_REASONS: Final[Dict[str, str]] = {
    _REASON_WAITING_FOR_SCHEDULE: "schedule",
    _REASON_ENERGY_LIMIT: "limit",
    _REASON_TIME_LIMIT: "limit",
    _REASON_COST_LIMIT: "limit",
    _REASON_SCHEDULE_ENERGY_LIMIT: "limit",
    _REASON_STOPPED_BY_USER: "stopped",
}


# REVE stops at a session limit in subState 9 (the code for waiting on an
# external start) instead of classic firmware's 2, 3 and 4, so the limit has to
# be read off the session: its on/off flag, its value, the session's own
# figure, and how far short of it a rounded reading may fall.
_LIMIT_CHECKS: Final[tuple[tuple[str, str, str, float, str], ...]] = (
    ("energyLimitS", "energyLimit", "sessionEnergy", 0.005, _REASON_ENERGY_LIMIT),
    ("timeLimitS", "timeLimit", "sessionTime", 1.0, _REASON_TIME_LIMIT),
    ("moneyLimitS", "moneyLimit", "sessionMoney", 0.005, _REASON_COST_LIMIT),
)


def reached_limit(read: Callable[[str], Optional[float]]) -> Optional[str]:
    """The reason for the first enabled session limit the session has reached.

    ``read`` answers a ``/main`` key (a number, or ``None`` when unreadable).
    Meant for REVE only: callers do not ask it on firmware that reports
    subState 2, 3 or 4 for the same event.
    """
    for flag, limit_key, session_key, tolerance, reason in _LIMIT_CHECKS:
        limit, session = read(limit_key), read(session_key)
        if read(flag) == 1 and limit and session is not None and session >= limit - tolerance:
            return reason
    return None


def not_charging_reason(
    state: Optional[int],
    substate: Optional[int],
    *,
    modern: bool,
    ocpp: bool,
    completed: bool,
    stopped: bool = False,
    limit_reached: Optional[str] = None,
) -> Optional[str]:
    """Fold state and subState into one closed set of reasons.

    ``completed`` is the coordinator's record that this stretch of state 5
    has shown subState 0 — the car ended the charge itself. subState is a live
    signal, not the stop cause: it reads "Schedule 1 Limit" all day outside
    the window, so a car that finished at 03:00 sees it flip at 07:00 while
    the state stays 5. Without that record, subState is what stopped it.

    ``stopped`` is the Stop switch. REVE holds a stopped charger in subState 9,
    which otherwise means waiting for an external start; classic firmware
    reads subState 1 there. ``limit_reached`` is the session limit REVE's
    subState 9 stands for when the session has reached one (``reached_limit``).
    """
    if state is None:
        return None
    # An unmapped firmware state collapses to "Unknown" rather than being
    # labelled with substate text that does not apply to it.
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
    if ocpp:
        return "Controlled by OCPP"
    # Only modern firmware's subState follows these maps. Firmware 1.x (GitHub
    # issue #11) has its own codes, so reading one there would name a confident
    # but arbitrary reason; the state-derived answer is the honest one.
    if not modern:
        if state == DEVICE_STATE_COMPLETE:
            return _REASON_CHARGE_COMPLETE
        return _REASON_WAITING_FOR_CAR if state == 3 else "Paused"
    # 9 is the charger holding for an external start command — a live setting,
    # so it wins even over a completion seen earlier.
    if substate == _SUBSTATE_ACTIVATION:
        if stopped:
            return _REASON_STOPPED_BY_USER
        return limit_reached or SUBSTATE_REASONS[_SUBSTATE_ACTIVATION]
    if state == DEVICE_STATE_COMPLETE and (completed or substate in (None, 0)):
        return _REASON_CHARGE_COMPLETE
    reason = SUBSTATE_REASONS.get(substate)
    if reason is not None:
        return reason
    # A code we cannot name is not the same as no code. subState 0 really does
    # mean "no limits", but an unmapped non-zero one means some limit IS
    # active — saying "nothing is holding it back" there would be a confident
    # lie. A missing field is neither; it falls through as absent data.
    if substate not in (None, 0):
        return "Unknown"
    # No limit is holding it back: Connected means the car has not asked for
    # current yet, Paused means the charger itself is idling.
    return _REASON_WAITING_FOR_CAR if state == 3 else "Paused"


def finish_reason(
    state: int,
    substate: Optional[int],
    *,
    modern: bool,
    ocpp: bool,
    stopped: bool = False,
    limit_reached: Optional[str] = None,
) -> str:
    """The ``reason`` of an ``eveus_charging_finished`` event.

    The event fires on the first poll of the new state, so no completion can
    have been seen in it yet: subState on that poll is the whole answer.
    """
    reason = not_charging_reason(
        state, substate, modern=modern, ocpp=ocpp, completed=False,  # pragma: no mutate - False and None are equally falsy; only read for truthiness
        stopped=stopped, limit_reached=limit_reached,
    )
    return SUBSTATE_FINISH_REASONS.get(reason) or FINISHED_REASONS.get(state, "stopped")
