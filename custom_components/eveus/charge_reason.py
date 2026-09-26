"""Why the charger is not charging — one answer for the sensor and the finish event.

The Not Charging Reason sensor and the ``eveus_charging_finished`` event both
answer "what stopped this charge". Kept in one place so the two can never name
the same stop differently.
"""
from __future__ import annotations

from typing import Dict, Final, Optional

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

NOT_CHARGING_REASON_OPTIONS: Final[tuple[str, ...]] = (
    "Charging",
    "Starting Up",
    "Cable Not Connected",
    "Waiting for Car",
    _REASON_CHARGE_COMPLETE,
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

# subState meaning while the charger holds off rather than delivering current.
# Schedule 1 and 2 collapse to one reason: which schedule fired is in the
# Schedule sensors.
SUBSTATE_REASONS: Final[Dict[int, str]] = {
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

_SUBSTATE_ACTIVATION: Final[int] = 9

# The finish-event reason for a stop the charger made on its own settings. Any
# other reason keeps the state-derived FINISHED_REASONS value it always had.
SUBSTATE_FINISH_REASONS: Final[Dict[str, str]] = {
    _REASON_WAITING_FOR_SCHEDULE: "schedule",
    "Energy Limit Reached": "limit",
    "Time Limit Reached": "limit",
    "Cost Limit Reached": "limit",
    _REASON_SCHEDULE_ENERGY_LIMIT: "limit",
    "Stopped by User": "stopped",
}


def not_charging_reason(
    state: Optional[int],
    substate: Optional[int],
    *,
    modern: bool,
    ocpp: bool,
    completed: bool,
) -> Optional[str]:
    """Fold state and subState into one closed set of reasons.

    ``completed`` is the coordinator's record that this stretch of state 5
    has shown subState 0 — the car ended the charge itself. subState is a live
    signal, not the stop cause: it reads "Schedule 1 Limit" all day outside
    the window, so a car that finished at 03:00 sees it flip at 07:00 while
    the state stays 5. Without that record, subState is what stopped it.
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
        return "Waiting for Car" if state == 3 else "Paused"
    # 9 is the charger holding for an external start command — a live setting,
    # so it wins even over a completion seen earlier.
    if substate == _SUBSTATE_ACTIVATION:
        return SUBSTATE_REASONS[_SUBSTATE_ACTIVATION]
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
    return "Waiting for Car" if state == 3 else "Paused"


def finish_reason(
    state: int,
    substate: Optional[int],
    *,
    modern: bool,
    ocpp: bool,
) -> str:
    """The ``reason`` of an ``eveus_charging_finished`` event.

    The event fires on the first poll of the new state, so no completion can
    have been seen in it yet: subState on that poll is the whole answer.
    """
    reason = not_charging_reason(
        state, substate, modern=modern, ocpp=ocpp, completed=False  # pragma: no mutate - False and None are equally falsy; only read for truthiness
    )
    return SUBSTATE_FINISH_REASONS.get(reason) or FINISHED_REASONS.get(state, "stopped")
