"""What REVE's measurement reads report about themselves, and the manual-mode
word the station gives, pinned from the poll down (survivors of the mutation
run on rc b52c8d8)."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from custom_components.eveus import reve
from test_reve_firmware import _Charger, _reve_main, _updater


def _poll(updater: Any) -> None:
    updater._reve._measurements_retry_at = 0.0  # the retry back-off is not under test
    asyncio.run(updater._async_update_data())


def _measurements(updater: Any) -> dict[str, Any]:
    return updater.reve_diagnostics()["measurements"]


def _known_reve(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, _Charger]:
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    _poll(updater)
    assert updater.firmware_family == "reve"
    return updater, charger


def test_a_station_that_reports_no_rates_object_is_still_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Older firmware answers ratesV2 with nothing: the read is good, not a crash."""
    updater, charger = _known_reve(monkeypatch)
    charger.rates = None
    _poll(updater)
    assert updater.last_update_success is True
    assert _measurements(updater)["graphql_failures"] == 0
    assert updater.snapshot.get("state") is not None


def test_failed_measurement_reads_are_counted_and_named(monkeypatch: pytest.MonkeyPatch) -> None:
    updater, charger = _known_reve(monkeypatch)
    charger.measurements_status = 500
    _poll(updater)
    assert _measurements(updater)["graphql_failures"] == 1
    assert updater.reve_diagnostics()["last_error"] == "ClientResponseError"
    _poll(updater)
    assert _measurements(updater)["graphql_failures"] == 2


def test_a_good_read_clears_the_failure_streak_and_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """One missed reply between good ones never turns REVE API off."""
    updater, charger = _known_reve(monkeypatch)
    for _ in range(3):
        charger.measurements_status = 500
        _poll(updater)
        charger.measurements_status = 200
        _poll(updater)
        assert updater.reve_diagnostics()["last_error"] is None
        assert updater.reve_api_answering is True
    charger.measurements_status = 500
    _poll(updater)
    assert updater.reve_api_answering is True
    _poll(updater)
    assert updater.reve_api_answering is False


def test_the_start_time_stays_put_while_the_station_keeps_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """Request latency moves a start time computed from the uptime by a second or
    two; the sensor must not follow it (a row per poll)."""
    clock = [1_000_000.0]
    monkeypatch.setattr(reve.time, "time", lambda: clock[0])
    updater, charger = _known_reve(monkeypatch)
    uptime = 3_600_000
    charger.station["stationUptimeMs"] = uptime
    _poll(updater)
    first = updater.reve_station_system["started_at"]
    clock[0] += 31.0
    charger.station["stationUptimeMs"] = uptime + 30_000  # the station thinks 30 s passed
    _poll(updater)
    assert updater.reve_station_system["started_at"] == first
    # A restart moves it.
    clock[0] += 300.0
    charger.station["stationUptimeMs"] = 5_000
    _poll(updater)
    assert updater.reve_station_system["started_at"] > first + 1000


def _payload(**fields: Any) -> dict[str, Any]:
    return {"state": 4, "sessionTime": 100, "oneCharge": 0, "evseEnabled": 0, **fields}


def test_a_command_from_home_assistant_outlives_a_read_without_the_flag() -> None:
    manual = reve.ManualMode()
    manual.report(False)  # the station said: normal mode
    manual.command(reve._COMMAND_STOP)
    manual.observe(_payload())
    assert manual.on is True


def test_leaving_manual_mode_stays_left_until_the_station_says_otherwise() -> None:
    manual = reve.ManualMode()
    manual.report(True)
    manual.observe(_payload())
    assert manual.on is True
    manual.left()
    manual.observe(_payload())
    assert manual.on is False
    manual.report(True)
    manual.observe(_payload())
    assert manual.on is True
