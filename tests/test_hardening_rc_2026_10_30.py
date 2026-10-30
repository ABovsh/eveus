"""Boundaries of the guards added in the 4.30.0 round.

Each guard keeps a reading at its exact limit and drops the first value past
it; a test that only feeds values far inside or far outside cannot tell `<`
from `<=`, which the mutation run showed for all of these.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.const import UnitOfEnergy, UnitOfTime
from homeassistant.exceptions import HomeAssistantError

from conftest import EveusTestUpdater, HelperHass, disable_state_writes
from custom_components.eveus import number as number_module
from custom_components.eveus.const import (
    MAX_RATE_HUNDREDTHS,
    MAX_SESSION_TIME_SECONDS,
    MAX_VALID_TIMEZONE_H,
    MIN_VALID_TIMEZONE_H,
)
from custom_components.eveus.ev_sensors import CachedSOCCalculator
from custom_components.eveus.number import EveusInitialSocNumber, _stored_session_clock
from custom_components.eveus.reve import _price
from custom_components.eveus.reve_fields import _local_seconds
from custom_components.eveus.session_history import (
    LastSessionDurationSensor,
    LastSessionEnergySensor,
)

_UTC_MIDNIGHT = "2026-10-30T00:00:00+00:00"
_UTC_MIDNIGHT_S = 1_793_318_400


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        (0, 0),
        (1, 1),
        (MAX_SESSION_TIME_SECONDS, MAX_SESSION_TIME_SECONDS),
        (MAX_SESSION_TIME_SECONDS + 1, None),
        (-1, None),
        (True, None),
        (120.0, None),
        ("120", None),
    ],
)
def test_a_stored_session_clock_is_kept_up_to_its_limits(stored, expected) -> None:
    """0 is the clock of a plug-in just seen; the longest session is valid too."""
    assert _stored_session_clock(stored) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, 0.0),
        (MAX_RATE_HUNDREDTHS / 100, MAX_RATE_HUNDREDTHS / 100),
        (MAX_RATE_HUNDREDTHS / 100 + 0.01, None),
        (-0.01, None),
        (True, None),
        (float("nan"), None),
        (float("inf"), None),
    ],
)
def test_a_station_price_is_kept_up_to_the_rate_bound(value, expected) -> None:
    """The highest rate the tariff numbers can hold is a price; a cent more is not."""
    assert _price(value) == expected


@pytest.mark.parametrize(
    ("zone", "expected"),
    [
        (MIN_VALID_TIMEZONE_H, _UTC_MIDNIGHT_S + MIN_VALID_TIMEZONE_H * 3600),
        (MAX_VALID_TIMEZONE_H, _UTC_MIDNIGHT_S + MAX_VALID_TIMEZONE_H * 3600),
        (0, _UTC_MIDNIGHT_S),
        (MIN_VALID_TIMEZONE_H - 1, None),
        (MAX_VALID_TIMEZONE_H + 1, None),
    ],
)
def test_the_charger_clock_accepts_every_real_offset(zone, expected) -> None:
    """Baker Island (UTC-12) and Kiribati (UTC+14) are both real zones."""
    assert _local_seconds(_UTC_MIDNIGHT, zone) == expected


@pytest.mark.parametrize(
    ("cls", "shown", "unit", "native"),
    [
        # 1.0001 min = 60.006 s: kept to two decimals, as the finished event publishes.
        (LastSessionDurationSensor, "1.0001", UnitOfTime.MINUTES, 60.01),
        # 1234.5678 Wh = 1.2345678 kWh.
        (LastSessionEnergySensor, "1234.5678", UnitOfEnergy.WATT_HOUR, 1.23),
    ],
)
def test_a_converted_restore_is_rounded_like_the_finished_event(cls, shown, unit, native) -> None:
    updater = MagicMock()
    updater.device_number = 1
    sensor = cls(updater, 1)
    state = SimpleNamespace(state=shown, attributes={"unit_of_measurement": unit})
    asyncio.run(sensor._async_restore_state(state))
    assert sensor.native_value == native


def test_an_impossible_initial_soc_names_the_entity(monkeypatch) -> None:
    monkeypatch.setattr(number_module, "async_dispatcher_send", lambda *a, **k: None)
    updater = EveusTestUpdater({})
    updater.config_entry = SimpleNamespace(entry_id="entry-id", data={})
    entity = EveusInitialSocNumber(updater, CachedSOCCalculator(), seed=20, device_number=1)
    entity.hass = HelperHass({})
    disable_state_writes(entity)

    with pytest.raises(HomeAssistantError, match="Initial SOC"):
        asyncio.run(entity.async_set_native_value(float("nan")))
    assert entity.native_value == 20
