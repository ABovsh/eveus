"""Bus events fired by the coordinator on charging state transitions."""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from custom_components.eveus.common_network import EveusUpdater
from custom_components.eveus.const import (
    EVENT_CAR_CONNECTED,
    EVENT_CAR_DISCONNECTED,
    EVENT_CHARGING_FINISHED,
    EVENT_CHARGING_STARTED,
    EVENT_ERROR,
)

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME


class _Hass:
    loop = None

    def __init__(self) -> None:
        self.bus = Mock()
        self.bus.async_fire = Mock()


def _updater(hass: _Hass) -> EveusUpdater:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, hass, device_number=2)
    # The fast-poll burst on transitions needs a running event loop; it is
    # exercised elsewhere (test_common_network) and irrelevant here.
    updater._schedule_post_command_refresh = Mock()
    return updater


def _fired(hass: _Hass, event: str) -> list[dict]:
    return [
        call.args[1]
        for call in hass.bus.async_fire.call_args_list
        if call.args[0] == event
    ]


def _poll(updater: EveusUpdater, payload: dict) -> None:
    updater._record_success(0.05, payload)


def test_started_fires_on_transition_into_charging() -> None:
    hass = _Hass()
    updater = _updater(hass)
    _poll(updater, {"state": 3})
    _poll(updater, {"state": 4})
    events = _fired(hass, EVENT_CHARGING_STARTED)
    assert len(events) == 1
    assert events[0]["device_number"] == 2


def test_no_started_on_first_poll_already_charging() -> None:
    """HA restart mid-session must not refire started."""
    hass = _Hass()
    updater = _updater(hass)
    _poll(updater, {"state": 4})
    assert not _fired(hass, EVENT_CHARGING_STARTED)


def test_repeat_polls_same_state_fire_nothing() -> None:
    hass = _Hass()
    updater = _updater(hass)
    _poll(updater, {"state": 4})
    _poll(updater, {"state": 4})
    assert not hass.bus.async_fire.called


def test_finished_complete_carries_session_snapshot_from_last_charging_poll() -> None:
    hass = _Hass()
    updater = _updater(hass)
    _poll(updater, {"state": 3})
    _poll(
        updater,
        {"state": 4, "sessionEnergy": 18.46, "sessionMoney": 49.78, "sessionTime": 22320},
    )
    # Firmware resets session fields on completion; snapshot must come from
    # the last poll where the session was still alive.
    _poll(updater, {"state": 5, "sessionEnergy": 0, "sessionMoney": 0, "sessionTime": 0})
    events = _fired(hass, EVENT_CHARGING_FINISHED)
    assert len(events) == 1
    evt = events[0]
    assert evt["reason"] == "complete"
    assert evt["session_energy_kwh"] == pytest.approx(18.46)
    assert evt["session_cost"] == pytest.approx(49.78)
    assert evt["session_duration_s"] == 22320
    assert evt["device_number"] == 2


@pytest.mark.parametrize(
    ("new_state", "reason"),
    [(2, "unplugged"), (3, "stopped"), (6, "paused")],
)
def test_finished_reason_classification(new_state: int, reason: str) -> None:
    hass = _Hass()
    updater = _updater(hass)
    _poll(updater, {"state": 4, "sessionEnergy": 1.0})
    _poll(updater, {"state": new_state})
    events = _fired(hass, EVENT_CHARGING_FINISHED)
    assert len(events) == 1
    assert events[0]["reason"] == reason


def test_charging_to_error_fires_error_not_finished() -> None:
    hass = _Hass()
    updater = _updater(hass)
    _poll(updater, {"state": 4})
    _poll(updater, {"state": 7, "subState": 5})
    assert not _fired(hass, EVENT_CHARGING_FINISHED)
    errors = _fired(hass, EVENT_ERROR)
    assert len(errors) == 1
    assert errors[0]["error_code"] == 5
    assert errors[0]["error_text"] == "Box Overheat"


def test_transition_across_offline_gap_is_silent() -> None:
    """connection lost mid-session: no finished/started events across the gap."""
    hass = _Hass()
    updater = _updater(hass)
    _poll(updater, {"state": 4})
    updater._record_failure(ValueError("boom"))
    _poll(updater, {"state": 2})
    assert not _fired(hass, EVENT_CHARGING_FINISHED)
    assert not _fired(hass, EVENT_CAR_DISCONNECTED)


def test_car_connected_and_disconnected() -> None:
    hass = _Hass()
    updater = _updater(hass)
    _poll(updater, {"state": 2})
    _poll(updater, {"state": 3})
    assert len(_fired(hass, EVENT_CAR_CONNECTED)) == 1
    _poll(updater, {"state": 2})
    assert len(_fired(hass, EVENT_CAR_DISCONNECTED)) == 1


def test_invalid_state_neither_fires_nor_breaks_tracking() -> None:
    hass = _Hass()
    updater = _updater(hass)
    _poll(updater, {"state": 4})
    _poll(updater, {"state": 99})
    _poll(updater, {"state": 4})
    assert not hass.bus.async_fire.called


# --- generated poll sequences: invariants that hold for every ordering ---

from hypothesis import example, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from custom_components.eveus.const import (  # noqa: E402
    CHARGING_STATES,
    DEVICE_STATE_CHARGING,
    DEVICE_STATE_ERROR,
)

_VALID_STATES = sorted(int(state) for state in CHARGING_STATES)
def _ok(states: list) -> st.SearchStrategy:
    return st.tuples(st.just("ok"), st.sampled_from(states), st.integers(min_value=0, max_value=1))


# Mostly valid polls: too many gaps or invalid states hide the transition
# chains (charge -> end -> another change) where duplicate events live.
_poll_step = st.one_of(
    _ok(_VALID_STATES), _ok(_VALID_STATES), _ok([99, None]), st.just(("fail",))
)


@settings(deadline=None, derandomize=True)
@given(st.lists(_poll_step, max_size=24))
# Pinned chains the generator must never be trusted to find by chance.
@example([("ok", 4, 0), ("ok", 5, 0), ("ok", 6, 0)])  # a second change after a finish
@example([("ok", 4, 0), ("ok", 99, 0), ("ok", 2, 0)])  # an unknown state mid-charge
@example([("ok", 2, 0), ("fail",), ("ok", 4, 0), ("fail",), ("ok", 2, 0)])  # gaps
def test_generated_poll_sequences_keep_the_event_contract(steps) -> None:
    hass = _Hass()
    updater = _updater(hass)
    fired = hass.bus.async_fire.call_args_list

    previous_valid: int | None = None  # last valid state since the last gap
    unfinished_charge = False  # a Charging sample seen since the gap / last finish
    energy = 0.0

    for step in steps:
        before = len(fired)
        if step[0] == "fail":
            updater._record_failure(TimeoutError())
            assert len(fired) == before, "a failed poll fired an event"
            previous_valid, unfinished_charge = None, False
            continue

        _, state, sub_state = step
        energy += 0.5
        payload = {"subState": sub_state, "sessionEnergy": energy}
        if state is not None:
            payload["state"] = state
        _poll(updater, payload)
        events = [call.args[0] for call in fired[before:]]

        if state not in _VALID_STATES:
            assert events == [], f"invalid state {state!r} fired {events}"
            continue
        if previous_valid is None:
            assert events == [], f"first poll after a gap fired {events}"
        if EVENT_CHARGING_STARTED in events:
            assert state == DEVICE_STATE_CHARGING and previous_valid not in (None, state)
        if EVENT_CHARGING_FINISHED in events:
            assert events.count(EVENT_CHARGING_FINISHED) == 1
            assert unfinished_charge, "finished without a charge observed since the gap"
            assert state not in (DEVICE_STATE_CHARGING, DEVICE_STATE_ERROR)
            unfinished_charge = False
        if EVENT_ERROR in events:
            assert state == DEVICE_STATE_ERROR
        if previous_valid is not None and previous_valid == state:
            assert set(events) <= {EVENT_ERROR}, f"unchanged state fired {events}"

        if state == DEVICE_STATE_CHARGING:
            unfinished_charge = True
        previous_valid = state
