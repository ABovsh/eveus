"""REVE manual mode: the station ignores schedules and every limit from the
moment the app's "ignore schedule" Start (``chargingCommand`` 0) or any Stop
(2) is given until the car is unplugged. The station reports no flag for it,
so the integration latches it: on at a 0 or 2, off when the car is
unplugged or the session counters start over (a station restart).
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from custom_components.eveus import reve
from test_reve_firmware import _Charger, _binary_setup, _classic_main, _reve_main, _updater
from test_reve_graphql_writes import _WritableCharger, _known, _send


def _payload(**fields: Any) -> dict[str, Any]:
    return {"state": 4, "sessionTime": 100, "oneCharge": 0, "evseEnabled": 0, **fields}


def test_a_session_starts_in_normal_mode() -> None:
    manual = reve.ManualMode()
    manual.observe(_payload())
    assert manual.on is False


@pytest.mark.parametrize("field", ["oneCharge", "evseEnabled"])
def test_charge_now_or_stop_latches_manual_mode(field: str) -> None:
    manual = reve.ManualMode()
    manual.observe(_payload(**{field: 1}))
    assert manual.on is True


def test_releasing_the_command_does_not_leave_manual_mode() -> None:
    manual = reve.ManualMode()
    manual.observe(_payload(oneCharge=1))
    manual.observe(_payload(sessionTime=160))
    assert manual.on is True


@pytest.mark.parametrize("state", [3, 4, 5, 6, 7])
def test_manual_mode_lasts_through_every_state_with_the_car_plugged_in(state: int) -> None:
    manual = reve.ManualMode()
    manual.observe(_payload(oneCharge=1))
    manual.observe(_payload(state=state, sessionTime=160))
    assert manual.on is True


@pytest.mark.parametrize("state", [0, 1, 2])
def test_unplugging_the_car_ends_manual_mode(state: int) -> None:
    manual = reve.ManualMode()
    manual.observe(_payload(oneCharge=1))
    manual.observe(_payload(state=state, sessionTime=0))
    assert manual.on is False


def test_a_command_seen_while_unplugged_does_not_latch() -> None:
    manual = reve.ManualMode()
    manual.observe(_payload(state=2, evseEnabled=1))
    assert manual.on is False


def test_session_counters_starting_over_end_manual_mode() -> None:
    # A station restart under a plugged-in car: sessionTime goes back to 0
    # and chargingCommand to 1; the station is in normal mode again.
    manual = reve.ManualMode()
    manual.observe(_payload(oneCharge=1, sessionTime=2000))
    manual.observe(_payload(sessionTime=5))
    assert manual.on is False


def test_a_missing_session_time_or_state_changes_nothing() -> None:
    manual = reve.ManualMode()
    manual.observe(_payload(oneCharge=1))
    manual.observe({})
    manual.observe({"sessionTime": None, "state": None})
    assert manual.on is True


def test_the_next_session_starts_clean_after_an_unplug() -> None:
    manual = reve.ManualMode()
    manual.observe(_payload(evseEnabled=1, sessionTime=900))
    manual.observe(_payload(state=2, sessionTime=0))
    manual.observe(_payload(state=3, sessionTime=0))
    manual.observe(_payload(sessionTime=30))
    assert manual.on is False


def test_a_command_written_from_home_assistant_latches_at_once() -> None:
    manual = reve.ManualMode()
    manual.command(reve._COMMAND_START)
    assert manual.on is False
    manual.command(reve._COMMAND_CHARGE_NOW)
    assert manual.on is True


def test_a_restored_manual_mode_is_kept_until_the_car_is_unplugged() -> None:
    manual = reve.ManualMode()
    manual.restore()
    manual.observe(_payload())
    assert manual.on is True
    manual.observe(_payload(state=2, sessionTime=0))
    assert manual.on is False



def test_a_restore_after_the_connector_was_pulled_out_is_dropped() -> None:
    """The sensor is added after the first poll; a car gone by then is not
    put back into manual mode until the next poll."""
    manual = reve.ManualMode()
    manual.observe(_payload(state=2, sessionTime=0))
    manual.restore(on_for=600)
    assert manual.on is False


def test_a_restore_into_a_session_younger_than_manual_mode_is_dropped() -> None:
    """Replugged while Home Assistant was down: the new session has not been
    in manual mode, and nothing else would end it before the next unplug."""
    manual = reve.ManualMode()
    manual.observe(_payload(sessionTime=300))
    manual.restore(on_for=3600)
    assert manual.on is False


def test_a_restore_into_the_same_session_is_kept() -> None:
    manual = reve.ManualMode()
    manual.observe(_payload(sessionTime=7200))
    manual.restore(on_for=3600)
    assert manual.on is True
    manual = reve.ManualMode()
    manual.observe(_payload(sessionTime=3590))
    manual.restore(on_for=3600)
    assert manual.on is True, "a few seconds of clock skew are not a new session"
    manual = reve.ManualMode()
    manual.observe({"state": 4})
    manual.restore(on_for=3600)
    assert manual.on is True, "an unknown session age keeps the restored state"

# ---------------------------------------------------------------------------
# Through the coordinator and the entity
# ---------------------------------------------------------------------------


def _plugged(charger: _Charger, state: int = 4, session_time: int = 600) -> None:
    charger.station["stationState"] = state
    charger.station["session"] = {**charger.station["session"], "sessionTime": session_time}


def test_the_coordinator_latches_a_command_it_reads_and_a_one_charge_it_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    charger, updater = _known(monkeypatch)
    _plugged(charger)
    asyncio.run(updater._async_update_data())
    assert updater.reve_manual_mode is False

    assert _send(updater, "oneCharge", 1) is True
    assert updater.reve_manual_mode is True

    assert _send(updater, "oneCharge", 0) is True
    asyncio.run(updater._async_update_data())
    assert updater.reve_manual_mode is True


def test_an_app_stop_read_from_the_station_latches(monkeypatch: pytest.MonkeyPatch) -> None:
    charger, updater = _known(monkeypatch)
    _plugged(charger)
    charger.charging_command = 2
    asyncio.run(updater._async_update_data())
    assert updater.reve_manual_mode is True

    _plugged(charger, state=2, session_time=0)
    charger.charging_command = 1
    asyncio.run(updater._async_update_data())
    assert updater.reve_manual_mode is False


def test_classic_firmware_has_no_manual_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_classic_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    assert updater.reve_manual_mode is None
    added, _ = _binary_setup(updater)
    assert "eveus_manual_mode" not in {e.unique_id for e in added}


def test_the_manual_mode_sensor_follows_the_latch(monkeypatch: pytest.MonkeyPatch) -> None:
    from conftest import disable_state_writes  # noqa: PLC0415

    charger = _WritableCharger(_reve_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    added, _ = _binary_setup(updater)
    sensor = next(e for e in added if e.unique_id == "eveus_manual_mode")
    disable_state_writes(sensor)
    sensor._maybe_finalize_device_info = lambda: None

    assert sensor.available is True
    assert sensor.is_on is False
    assert sensor.entity_category is None
    assert sensor.device_class is None

    _plugged(charger)
    asyncio.run(updater._async_update_data())
    assert _send(updater, "evseEnabled", 1) is True
    assert sensor.is_on is True


def test_the_manual_mode_sensor_restores_its_last_state(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    charger = _WritableCharger(_reve_main())
    _plugged(charger, state=5)
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    added, _ = _binary_setup(updater)
    sensor = next(e for e in added if e.unique_id == "eveus_manual_mode")

    asyncio.run(sensor._async_restore_state(SimpleNamespace(state="unavailable")))
    assert updater.reve_manual_mode is False
    asyncio.run(sensor._async_restore_state(SimpleNamespace(state="on")))
    assert updater.reve_manual_mode is True


def test_the_manual_mode_sensor_passes_how_long_it_was_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import timedelta
    from types import SimpleNamespace

    from homeassistant.util import dt as dt_util

    charger = _WritableCharger(_reve_main())
    _plugged(charger, state=5, session_time=300)
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    added, _ = _binary_setup(updater)
    sensor = next(e for e in added if e.unique_id == "eveus_manual_mode")
    on_since = dt_util.utcnow() - timedelta(hours=2)
    asyncio.run(sensor._async_restore_state(SimpleNamespace(state="on", last_changed=on_since)))
    assert updater.reve_manual_mode is False


def test_a_restore_exactly_at_the_slack_is_kept() -> None:
    manual = reve.ManualMode()
    manual.observe(_payload(sessionTime=3600 - reve._RESTORE_SLACK_SECONDS))
    manual.restore(on_for=3600)
    assert manual.on is True


# ---------------------------------------------------------------------------
# REVE_PLUS 1.0.3 reports the mode itself: stationData.chargingManualControl
# ---------------------------------------------------------------------------


def test_the_station_flag_overrides_the_latch_both_ways() -> None:
    manual = reve.ManualMode()
    manual.observe(_payload(evseEnabled=1))
    manual.report(False)
    manual.observe(_payload(evseEnabled=1))
    assert manual.on is False, "the station says it left manual mode"
    manual.report(True)
    manual.observe(_payload())
    assert manual.on is True


def test_without_the_flag_the_latch_carries_on_from_the_last_flag() -> None:
    manual = reve.ManualMode()
    manual.report(False)
    manual.observe(_payload(evseEnabled=1))
    manual.report(None)
    manual.observe(_payload(sessionTime=160))
    assert manual.on is False, "a /main poll keeps the station's last word"


@pytest.mark.parametrize("value", [None, 0, 1, "true"])
def test_anything_but_a_boolean_is_no_flag(value: Any) -> None:
    manual = reve.ManualMode()
    manual.observe(_payload(oneCharge=1))
    manual.report(value)
    manual.observe(_payload(sessionTime=160))
    assert manual.on is True


def test_a_command_from_home_assistant_shows_until_the_next_read() -> None:
    manual = reve.ManualMode()
    manual.report(False)
    manual.observe(_payload())
    manual.command(reve._COMMAND_STOP)
    assert manual.on is True
    manual.report(False)
    manual.observe(_payload())
    assert manual.on is False


def test_a_restore_after_the_station_reported_is_ignored() -> None:
    manual = reve.ManualMode()
    manual.report(False)
    manual.observe(_payload())
    manual.restore()
    assert manual.on is False


def test_the_coordinator_reads_the_station_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    charger, updater = _known(monkeypatch)
    _plugged(charger)
    charger.charging_command = 2
    charger.station["chargingManualControl"] = False
    asyncio.run(updater._async_update_data())
    assert updater.reve_manual_mode is False

    charger.charging_command = 1
    charger.station["chargingManualControl"] = True
    asyncio.run(updater._async_update_data())
    assert updater.reve_manual_mode is True


def test_the_measurements_read_asks_for_the_flag_but_does_not_require_it() -> None:
    assert "chargingManualControl" in reve.MEASUREMENTS_QUERY
    assert ("chargingManualControl",) not in reve._EXPECTED_PATHS
