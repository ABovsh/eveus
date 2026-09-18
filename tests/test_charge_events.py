"""Charging/error transition events fired by the coordinator: error sub-state
re-fire, fault-ended sessions, poll gaps, auth gaps, and payload bounds."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from custom_components.eveus.common_network import EveusUpdater
from custom_components.eveus.const import EVENT_CHARGING_FINISHED
from custom_components.eveus.session_history import LastSessionEnergySensor
from custom_components.eveus.const import EVENT_ERROR
import json
from homeassistant.exceptions import ConfigEntryAuthFailed
from conftest import EveusTestUpdater
from custom_components.eveus import common_network
from custom_components.eveus.const import (
    DEVICE_STATE_CHARGING,
    DEVICE_STATE_STANDBY,
)
from test_common_network import (  # noqa: E402
    TEST_HOST,
    TEST_PASSWORD,
    TEST_USERNAME,
    _FakeBus,
)
from test_soc_autofill import (  # noqa: E402
    _no_dispatcher,  # noqa: F401 - autouse fixture, applies by being imported here
    )
from custom_components.eveus.session_history import (
    LastSessionCostSensor,
    LastSessionDurationSensor,
)


class _Hass:
    loop = None

    def __init__(self) -> None:
        self.bus = Mock()
        self.bus.async_fire = Mock()


def _updater(hass: _Hass) -> EveusUpdater:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, hass, device_number=1)
    updater._schedule_post_command_refresh = Mock()
    return updater


def _finished_events(hass: _Hass) -> list[dict]:
    return [
        call.args[1]
        for call in hass.bus.async_fire.call_args_list
        if call.args[0] == EVENT_CHARGING_FINISHED
    ]


def _finish_session(charging_payload: dict) -> list[dict]:
    hass = _Hass()
    updater = _updater(hass)
    updater._record_success(0.05, {"state": 4, **charging_payload})
    updater._record_success(0.05, {"state": 5})
    return _finished_events(hass)


def test_finished_event_drops_negative_session_energy() -> None:
    events = _finish_session({"sessionEnergy": -5.0, "sessionMoney": 12.5})
    assert len(events) == 1
    assert events[0]["session_energy_kwh"] is None
    assert events[0]["session_cost"] == pytest.approx(12.5)


def test_finished_event_drops_absurd_session_cost_and_duration() -> None:
    events = _finish_session(
        {"sessionEnergy": 18.4, "sessionMoney": 1e12, "sessionTime": -30}
    )
    assert len(events) == 1
    assert events[0]["session_energy_kwh"] == pytest.approx(18.4)
    assert events[0]["session_cost"] is None
    assert events[0]["session_duration_s"] is None


def test_finished_event_keeps_valid_snapshot_values() -> None:
    events = _finish_session(
        {"sessionEnergy": 18.4, "sessionMoney": 49.78, "sessionTime": 22320}
    )
    assert len(events) == 1
    assert events[0]["session_energy_kwh"] == pytest.approx(18.4)
    assert events[0]["session_cost"] == pytest.approx(49.78)
    assert events[0]["session_duration_s"] == 22320

def _updater_07_19(hass: _Hass) -> EveusUpdater:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, hass, device_number=2)
    updater._schedule_post_command_refresh = Mock()
    return updater


def _error_events(hass: _Hass) -> list[dict]:
    return [
        call.args[1]
        for call in hass.bus.async_fire.call_args_list
        if call.args[0] == EVENT_ERROR
    ]


def test_error_refires_when_substate_changes_within_error() -> None:
    """A new fault code while the charger stays in Error must reach HA."""
    hass = _Hass()
    updater = _updater_07_19(hass)
    updater._record_success(0.05, {"state": 2})
    updater._record_success(0.05, {"state": 7, "subState": 1})
    updater._record_success(0.05, {"state": 7, "subState": 3})
    events = _error_events(hass)
    assert [e["error_code"] for e in events] == [1, 3]


def test_error_does_not_refire_on_same_substate() -> None:
    hass = _Hass()
    updater = _updater_07_19(hass)
    updater._record_success(0.05, {"state": 2})
    updater._record_success(0.05, {"state": 7, "subState": 1})
    updater._record_success(0.05, {"state": 7, "subState": 1})
    assert len(_error_events(hass)) == 1


def test_error_substate_memory_resets_after_leaving_error() -> None:
    """Error → OK → same Error code again is a NEW fault and must fire."""
    hass = _Hass()
    updater = _updater_07_19(hass)
    updater._record_success(0.05, {"state": 2})
    updater._record_success(0.05, {"state": 7, "subState": 1})
    updater._record_success(0.05, {"state": 2})
    updater._record_success(0.05, {"state": 7, "subState": 1})
    assert len(_error_events(hass)) == 2


def test_error_substate_memory_survives_no_offline_gap_rule() -> None:
    """An offline gap clears state memory; re-entering Error stays silent
    on the first poll (previous is None) — unchanged contract."""
    hass = _Hass()
    updater = _updater_07_19(hass)
    updater._record_success(0.05, {"state": 2})
    updater._record_success(0.05, {"state": 7, "subState": 1})
    updater._record_failure(ValueError("offline"))
    updater._record_success(0.05, {"state": 7, "subState": 4})
    assert len(_error_events(hass)) == 1

class _Response:
    def __init__(self, status: int) -> None:
        self.status = status

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def text(self) -> str:
        return json.dumps({"state": 2})

    @property
    def content_length(self) -> int | None:
        return len(json.dumps({"state": 2}).encode())

    @property
    def headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}


class _Session:
    def __init__(self, response: _Response) -> None:
        self._response = response

    def post(self, *args: object, **kwargs: object) -> _Response:
        return self._response


def _updater_after_401(monkeypatch: pytest.MonkeyPatch) -> EveusUpdater:
    """Seed the transition memory, then take a 401 on the next poll."""
    monkeypatch.setattr(
        common_network, "async_get_clientsession", lambda hass: _Session(_Response(401))
    )
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._schedule_post_command_refresh = Mock()
    updater._event_prev_state = DEVICE_STATE_CHARGING
    updater._event_prev_error_code = 7
    updater._legacy_charging_latched = True
    updater._legacy_zero_power_polls = 1

    with pytest.raises(ConfigEntryAuthFailed):
        asyncio.run(updater._async_update_data())
    return updater


def test_auth_failure_clears_transition_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 401 is an offline gap for event purposes, exactly like a timeout."""
    updater = _updater_after_401(monkeypatch)

    assert updater._event_prev_state is None
    assert updater._event_charging_payload is None
    assert updater._event_prev_error_code is None
    assert updater._legacy_charging_latched is False
    assert updater._legacy_zero_power_polls == 0


def test_no_stale_charging_finished_after_auth_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-401 payload must not become a Last Session snapshot afterwards."""
    updater = _updater_after_401(monkeypatch)

    # Recovery poll (Force Refresh): the session ended during the auth gap, so
    # the transition is unobserved and must stay silent.
    updater._record_success(0.05, {"state": DEVICE_STATE_STANDBY})

    finished = [
        call.args[1]
        for call in updater.hass.bus.async_fire.call_args_list
        if call.args[0] == EVENT_CHARGING_FINISHED
    ]
    assert finished == []

def _updater_with_bus():
    hass = SimpleNamespace(bus=_FakeBus(), is_stopping=False, loop=None)
    return EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, hass), hass.bus


_CHARGING_POLL = {
    "state": common_network.DEVICE_STATE_CHARGING,
    "sessionEnergy": 9.5,
    "sessionMoney": 4.75,
    "sessionTime": 1800,
}


def test_charging_finished_fires_when_a_fault_ends_the_session() -> None:
    """Charging → Error → Charge Complete must still report the session."""
    updater, bus = _updater_with_bus()
    updater._event_prev_state = common_network.DEVICE_STATE_STANDBY

    updater._emit_transition_events(dict(_CHARGING_POLL))
    updater._emit_transition_events({"state": common_network.DEVICE_STATE_ERROR, "subState": 2})
    # 5 = Charge Complete: stays inside CONNECTED_STATES, so no plug event.
    updater._emit_transition_events({"state": 5})

    finished = [payload for name, payload in bus.fired if name == common_network.EVENT_CHARGING_FINISHED]
    assert finished == [
        {
            "device_number": 1,
            "reason": "complete",
            "session_energy_kwh": 9.5,
            "session_cost": 4.75,
            "session_duration_s": 1800,
        }
    ]


def test_charging_finished_does_not_fire_when_the_fault_clears_back_to_charging() -> None:
    """A fault the charger recovers from mid-session did not end the session."""
    updater, bus = _updater_with_bus()
    updater._event_prev_state = common_network.DEVICE_STATE_STANDBY

    updater._emit_transition_events(dict(_CHARGING_POLL))
    updater._emit_transition_events({"state": common_network.DEVICE_STATE_ERROR, "subState": 2})
    updater._emit_transition_events(dict(_CHARGING_POLL))

    assert common_network.EVENT_CHARGING_FINISHED not in [name for name, _ in bus.fired]


def test_error_to_idle_without_a_prior_session_stays_silent() -> None:
    """An Error seen before any charging poll must not fabricate a session."""
    updater, bus = _updater_with_bus()
    updater._event_prev_state = common_network.DEVICE_STATE_ERROR

    updater._emit_transition_events({"state": common_network.DEVICE_STATE_STANDBY})

    assert common_network.EVENT_CHARGING_FINISHED not in [name for name, _ in bus.fired]


def test_a_poll_gap_forgets_the_pending_session_snapshot() -> None:
    """Transitions across an offline gap stay silent — including this one."""
    updater, bus = _updater_with_bus()
    updater._event_prev_state = common_network.DEVICE_STATE_STANDBY

    updater._emit_transition_events(dict(_CHARGING_POLL))
    updater._emit_transition_events({"state": common_network.DEVICE_STATE_ERROR, "subState": 2})
    updater._forget_poll_gap_state()
    updater._emit_transition_events({"state": common_network.DEVICE_STATE_ERROR, "subState": 2})
    updater._emit_transition_events({"state": 5})

    assert common_network.EVENT_CHARGING_FINISHED not in [name for name, _ in bus.fired]

@pytest.mark.parametrize("sensor_class", [LastSessionEnergySensor, LastSessionCostSensor, LastSessionDurationSensor])
@pytest.mark.parametrize("value", [10**400, -(10**400)], ids=["huge-positive", "huge-negative"])
def test_public_session_event_rejects_oversized_integer(sensor_class, value):
    """The event listener must reject oversized external values without raising."""
    sensor = sensor_class(EveusTestUpdater({}))
    sensor._handle_finished_event(SimpleNamespace(data={
        "device_number": 1,
        sensor._event_field: value,
        "reason": "complete",
    }))
    assert sensor.native_value is None
