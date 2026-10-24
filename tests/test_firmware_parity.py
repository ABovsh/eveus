"""What the 2026-10 ``rc`` work leaves alone on the older firmwares.

The ``rc`` changes were made for REVE_PLUS 1.0.3, but most of the code runs for
every charger. These tests state what a classic Eveus charger and a REVE_PLUS
1.0.0 station (which answers none of the 1.0.3 fields) still get, so a change
meant for one cannot reach the others unnoticed.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.eveus import reve
from custom_components.eveus.control_base import command_failure
from test_reve_firmware import _Charger, _classic_main, _clock, _reve_main, _updater

# ---------------------------------------------------------------------------
# Classic Eveus firmware
# ---------------------------------------------------------------------------


def _classic(monkeypatch: pytest.MonkeyPatch) -> tuple[_Charger, Any]:
    charger = _Charger(_classic_main())
    return charger, _updater(monkeypatch, charger)


@pytest.mark.parametrize(
    ("state", "adaptive"), [(2, 0), (4, 0), (4, 1), (6, 1), (5, 1)],
    ids=["idle", "charging", "adaptive charge", "adaptive pause", "stopped"],
)
def test_a_classic_charger_is_asked_main_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch, state: int, adaptive: int,
) -> None:
    charger, updater = _classic(monkeypatch)
    charger.main["state"] = state
    charger.main["aiStatus"] = adaptive

    for _ in range(12):
        asyncio.run(updater._async_update_data())

    assert charger.paths() == ["/main"] * 12


def test_a_classic_charger_has_none_of_the_reve_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _charger, updater = _classic(monkeypatch)
    asyncio.run(updater._async_update_data())

    assert updater.firmware_family == reve.FAMILY_CLASSIC
    assert updater.reve_schedules() is None
    assert updater.reve_card_schedules() == {"reve": False}
    assert updater.reve_station_system == {}
    assert updater.reve_missing_fields == ()
    assert updater.reve_manual_mode_can_turn_off is False
    assert updater.last_command_failure is None
    assert updater.reve_api_answering is None


def test_a_classic_failure_message_is_the_one_it_always_was() -> None:
    updater = type("Classic", (), {"last_command_failure": None})()
    error = command_failure(updater, "Eveus charger did not accept 'Sync Time'")
    assert isinstance(error, HomeAssistantError)
    assert str(error) == "Eveus charger did not accept 'Sync Time'"


def test_a_classic_meter_that_drops_is_held_for_two_polls_then_believed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The charger's own counter reset (its web page) still reaches Home
    # Assistant, three polls later instead of at once; growth is never held.
    charger, updater = _classic(monkeypatch)
    charger.main["totalEnergy"] = 100.0
    assert asyncio.run(updater._async_update_data())["totalEnergy"] == 100.0
    charger.main["totalEnergy"] = 100.5
    assert asyncio.run(updater._async_update_data())["totalEnergy"] == 100.5

    charger.main["totalEnergy"] = 0.0
    held = [asyncio.run(updater._async_update_data())["totalEnergy"] for _ in range(3)]

    assert held == [100.5, 100.5, 0.0]


# ---------------------------------------------------------------------------
# REVE_PLUS 1.0.0: a station that answers none of the 1.0.3 fields
# ---------------------------------------------------------------------------


def _station_1_0_0(monkeypatch: pytest.MonkeyPatch) -> tuple[_Charger, Any]:
    charger = _Charger(_reve_main())
    assert not set(reve._STATION_SYSTEM_FIELDS) & set(charger.station)
    return charger, _updater(monkeypatch, charger)


def test_a_station_without_the_1_0_3_fields_never_raises_the_missing_fields_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clock(monkeypatch)
    charger, updater = _station_1_0_0(monkeypatch)
    for _ in range(reve.FIELDS_MISSING_READS + 3):
        asyncio.run(updater._async_update_data())

    assert updater.reve_missing_fields == ()
    assert updater._reve.manual_mode.supported is False
    assert updater.reve_manual_mode_can_turn_off is False
    assert updater.reve_station_system == {}


def test_a_station_without_the_1_0_3_fields_costs_72_requests_an_idle_hour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = _clock(monkeypatch)
    charger, updater = _station_1_0_0(monkeypatch)
    asyncio.run(updater._async_update_data())
    charger.calls.clear()

    for _ in range(60):
        now[0] += 60
        asyncio.run(updater._async_update_data())

    assert charger.paths().count("/graphql") == 60
    assert charger.paths().count("/main") == 12


def test_a_station_without_the_1_0_3_fields_is_never_told_to_leave_manual_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clock(monkeypatch)
    charger, updater = _station_1_0_0(monkeypatch)
    asyncio.run(updater._async_update_data())
    charger.calls.clear()

    asyncio.run(updater.send_command("evseEnabled", 1))
    asyncio.run(updater.send_command("evseEnabled", 0))

    writes = [str(body) for path, body in charger.calls if path == "/graphql"]
    assert any("chargingCommand" in write for write in writes)
    assert all("chargingManualControl" not in write for write in writes)
