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
