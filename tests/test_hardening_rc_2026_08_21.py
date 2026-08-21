"""Hardening tests for the 2026-08-21 audit round."""
from __future__ import annotations

from conftest import EV_HELPERS, EveusTestUpdater
from custom_components.eveus.ev_sensors import EVSocKwhSensor, EVSocPercentSensor

from test_ev_sensor_entities import push_helpers


def _sensor(cls, data: dict):
    sensor = cls(EveusTestUpdater(data))
    push_helpers(sensor._soc_calculator, EV_HELPERS)
    return sensor


def test_soc_kwh_goes_unknown_when_session_energy_absent_mid_session() -> None:
    """An active session with sessionEnergy missing must not read as 0 kWh delivered."""
    sensor = _sensor(EVSocKwhSensor, {"state": 4})
    assert sensor._get_sensor_value() is None


def test_soc_percent_goes_unknown_when_session_energy_absent_mid_session() -> None:
    """Same for SOC Percent: 0 delivered would snap the graph down to Initial SOC."""
    sensor = _sensor(EVSocPercentSensor, {"state": 4})
    assert sensor._get_sensor_value() is None


def test_soc_sensors_still_use_zero_fallback_outside_a_session() -> None:
    """Before a session starts, an absent sessionEnergy still means 0 delivered."""
    kwh = _sensor(EVSocKwhSensor, {"state": 2})
    pct = _sensor(EVSocPercentSensor, {"state": 2})
    assert kwh._get_sensor_value() is not None
    assert pct._get_sensor_value() == 20


# --- A session ended by a fault must still report its summary ----------------

from types import SimpleNamespace  # noqa: E402

from custom_components.eveus import common_network  # noqa: E402
from custom_components.eveus.common_network import EveusUpdater  # noqa: E402

from test_common_network import (  # noqa: E402
    TEST_HOST,
    TEST_PASSWORD,
    TEST_USERNAME,
    _FakeBus,
)


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
    updater._event_prev_payload = {"state": common_network.DEVICE_STATE_STANDBY}

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
    updater._event_prev_payload = {"state": common_network.DEVICE_STATE_STANDBY}

    updater._emit_transition_events(dict(_CHARGING_POLL))
    updater._emit_transition_events({"state": common_network.DEVICE_STATE_ERROR, "subState": 2})
    updater._emit_transition_events(dict(_CHARGING_POLL))

    assert common_network.EVENT_CHARGING_FINISHED not in [name for name, _ in bus.fired]


def test_error_to_idle_without_a_prior_session_stays_silent() -> None:
    """An Error seen before any charging poll must not fabricate a session."""
    updater, bus = _updater_with_bus()
    updater._event_prev_state = common_network.DEVICE_STATE_ERROR
    updater._event_prev_payload = {"state": common_network.DEVICE_STATE_ERROR}

    updater._emit_transition_events({"state": common_network.DEVICE_STATE_STANDBY})

    assert common_network.EVENT_CHARGING_FINISHED not in [name for name, _ in bus.fired]


def test_a_poll_gap_forgets_the_pending_session_snapshot() -> None:
    """Transitions across an offline gap stay silent — including this one."""
    updater, bus = _updater_with_bus()
    updater._event_prev_state = common_network.DEVICE_STATE_STANDBY
    updater._event_prev_payload = {"state": common_network.DEVICE_STATE_STANDBY}

    updater._emit_transition_events(dict(_CHARGING_POLL))
    updater._emit_transition_events({"state": common_network.DEVICE_STATE_ERROR, "subState": 2})
    updater._forget_poll_gap_state()
    updater._emit_transition_events({"state": common_network.DEVICE_STATE_ERROR, "subState": 2})
    updater._emit_transition_events({"state": 5})

    assert common_network.EVENT_CHARGING_FINISHED not in [name for name, _ in bus.fired]


# --- The public charging-finished event is not trusted for its reason -------

import custom_components.eveus.session_history as session_history  # noqa: E402
from custom_components.eveus.const import FINISHED_REASONS  # noqa: E402


def _last_session_sensor():
    from unittest.mock import MagicMock

    updater = MagicMock()
    updater.device_number = 1
    sensor = session_history.LastSessionEnergySensor(updater, 1)
    sensor.hass = None
    return sensor


def _finished_event(reason):
    return SimpleNamespace(
        data={
            "device_number": 1,
            "reason": reason,
            "session_energy_kwh": 18.46,
        }
    )


def test_last_session_reason_rejects_a_value_outside_the_closed_set() -> None:
    """`_value_from_event` re-checks the numbers because the bus is public;
    the reason string gets the same treatment."""
    sensor = _last_session_sensor()
    sensor._handle_finished_event(_finished_event("x" * 5000))
    assert sensor.extra_state_attributes["reason"] is None


def test_last_session_reason_rejects_a_non_string() -> None:
    sensor = _last_session_sensor()
    sensor._handle_finished_event(_finished_event({"nested": "junk"}))
    assert sensor.extra_state_attributes["reason"] is None


def test_last_session_reason_keeps_every_reason_the_coordinator_fires() -> None:
    for reason in (*FINISHED_REASONS.values(), "stopped"):
        sensor = _last_session_sensor()
        sensor._handle_finished_event(_finished_event(reason))
        assert sensor.extra_state_attributes["reason"] == reason
