"""A charger that is still starting reports its lifetime meters as 0.

REVE_PLUS 1.0.3 did on 2026-10-23 22:42:39: one poll with Total Energy,
Counter A/B energy and Counter B cost all at 0, then the real values again. A
``total_increasing`` sensor reads that dip as a meter reset, so Home
Assistant's statistics and every utility meter on top counted the whole
lifetime again (5440 kWh in one day). A drop is therefore held until it
repeats, or until Home Assistant itself reset that counter.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from custom_components.eveus.meter_guard import METER_DROP_CONFIRM_POLLS, MeterGuard
from test_reve_firmware import _Charger, _classic_main, _updater

_METERS = {"totalEnergy": 5440.86, "IEM1": 49.16, "IEM2": 3845.51, "IEM1_money": 0, "IEM2_money": 12983.09}


def test_a_one_poll_dip_to_zero_is_held() -> None:
    guard = MeterGuard()
    assert guard.apply(dict(_METERS)) == _METERS
    zero = guard.apply({key: 0 for key in _METERS} | {"state": 3})
    assert zero == _METERS | {"state": 3}
    assert guard.apply(dict(_METERS)) == _METERS


def test_growth_passes_through() -> None:
    guard = MeterGuard()
    guard.apply({"totalEnergy": 10.0})
    assert guard.apply({"totalEnergy": 10.5}) == {"totalEnergy": 10.5}


def test_a_drop_that_repeats_is_accepted() -> None:
    """A counter reset in the charger's app, or a replaced board."""
    guard = MeterGuard()
    guard.apply({"IEM1": 49.16})
    for _ in range(METER_DROP_CONFIRM_POLLS - 1):
        assert guard.apply({"IEM1": 0})["IEM1"] == 49.16
    assert guard.apply({"IEM1": 0})["IEM1"] == 0
    assert guard.apply({"IEM1": 0.2})["IEM1"] == 0.2


def test_the_count_starts_over_when_the_real_value_returns() -> None:
    guard = MeterGuard()
    guard.apply({"IEM1": 49.16})
    for _ in range(METER_DROP_CONFIRM_POLLS - 1):
        guard.apply({"IEM1": 0})
    guard.apply({"IEM1": 49.2})
    assert guard.apply({"IEM1": 0})["IEM1"] == 49.2


def test_a_reset_from_home_assistant_shows_at_once() -> None:
    guard = MeterGuard()
    guard.apply({"IEM1": 49.16, "IEM1_money": 120.0, "IEM2": 3845.51})
    guard.expect_reset("rstEM1")
    out = guard.apply({"IEM1": 0, "IEM1_money": 0, "IEM2": 0})
    assert out == {"IEM1": 0, "IEM1_money": 0, "IEM2": 3845.51}
    assert guard.apply({"IEM1": 49.16})["IEM1"] == 49.16, "the reset was taken once"
    assert guard.apply({"IEM1": 0})["IEM1"] == 49.16


@pytest.mark.parametrize("value", [None, "x", True])
def test_what_is_not_a_number_is_left_to_the_payload_rules(value: Any) -> None:
    guard = MeterGuard()
    guard.apply({"totalEnergy": 10.0})
    assert guard.apply({"totalEnergy": value}) == {"totalEnergy": value}
    assert guard.apply({}) == {}
    assert guard.apply({"totalEnergy": 9.0})["totalEnergy"] == 10.0


def test_the_coordinator_holds_a_boot_dip(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_classic_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    before = updater.snapshot.get("totalEnergy")
    assert before
    charger.main = {**charger.main, "totalEnergy": 0, "IEM1": 0, "IEM2": 0}
    asyncio.run(updater._async_update_data())
    assert updater.snapshot.get("totalEnergy") == before


def test_a_counter_reset_sent_from_home_assistant_is_not_held(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_classic_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    assert updater.snapshot.get("IEM1")
    assert asyncio.run(updater.send_command("rstEM1", 0, retry=False)) is True
    charger.main = {**charger.main, "IEM1": 0}
    asyncio.run(updater._async_update_data())
    assert updater.snapshot.get("IEM1") == 0
