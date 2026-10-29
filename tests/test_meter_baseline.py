"""A charger that boots with its lifetime meters at 0 must not be believed on
the first poll after Home Assistant started, either.

``MeterGuard`` holds a dip only against a reading it has already accepted, and
that memory lived in the updater: a restart or reload of the entry (the charger
is usually switched off by then) began without it, so the first poll after the
charger came up could be the zero poll, and the zero became the baseline.
Home Assistant's statistics then counted the lifetime again, as on 2026-10-23.
The last accepted readings are therefore kept in storage.
"""
from __future__ import annotations

import asyncio
import math
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.eveus import common_network
from custom_components.eveus.common_network import EveusUpdater
from custom_components.eveus.meter_guard import METER_SAVE_INTERVAL_SECONDS, MeterGuard
from test_reve_firmware import TEST_HOST, TEST_PASSWORD, TEST_USERNAME, _Charger, _classic_main, _Hass, _no_sleep

_SAVED = {"totalEnergy": 5440.86, "IEM1": 49.16, "IEM2": 3845.51, "IEM1_money": 120.5, "IEM2_money": 12983.09}


class _FakeStore:
    """Home Assistant's ``Store``: what ``async_load`` hands back, and what a
    delayed save would write."""

    loaded: Any = None
    instances: list["_FakeStore"] = []

    def __init__(self, hass: Any, version: int, key: str) -> None:
        self.hass, self.version, self.key = hass, version, key
        self.saves: list[dict[str, Any]] = []
        self.removed = False
        _FakeStore.instances.append(self)

    async def async_load(self) -> Any:
        return _FakeStore.loaded

    def async_delay_save(self, data_func: Any, delay: float = 0) -> None:
        assert delay > 0, "a delayed save needs a delay"
        self.saves.append(data_func())

    async def async_save(self, data: Any) -> None:
        self.saves.append(dict(data))

    async def async_remove(self) -> None:
        self.removed = True


@pytest.fixture(autouse=True)
def _store(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeStore.loaded, _FakeStore.instances = None, []
    monkeypatch.setattr(common_network, "Store", _FakeStore)


def _updater(monkeypatch: pytest.MonkeyPatch, charger: _Charger) -> EveusUpdater:
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: charger)
    monkeypatch.setattr("custom_components.eveus.common_command.asyncio.sleep", _no_sleep)
    entry = SimpleNamespace(entry_id="entry-1", async_on_unload=lambda callback: None)
    return EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass(), config_entry=entry)


def _boot_zero() -> dict[str, Any]:
    return {**_classic_main(), **{key: 0 for key in _SAVED}}


def test_the_first_poll_after_a_restart_is_held_against_the_saved_readings(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeStore.loaded = dict(_SAVED)
    updater = _updater(monkeypatch, _Charger(_boot_zero()))
    asyncio.run(updater.async_load_meter_baseline())

    asyncio.run(updater._async_update_data())

    for key, value in _SAVED.items():
        assert updater.snapshot.get(key) == value, key


def test_without_saved_readings_the_first_poll_is_believed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A new charger, or a first install: zero is a valid reading."""
    updater = _updater(monkeypatch, _Charger(_boot_zero()))
    asyncio.run(updater.async_load_meter_baseline())

    asyncio.run(updater._async_update_data())

    assert updater.snapshot.get("totalEnergy") == 0


def test_a_saved_baseline_does_not_hide_a_confirmed_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    """A replaced board while Home Assistant was down: the zero repeats."""
    _FakeStore.loaded = dict(_SAVED)
    updater = _updater(monkeypatch, _Charger(_boot_zero()))
    asyncio.run(updater.async_load_meter_baseline())

    for _ in range(3):
        asyncio.run(updater._async_update_data())

    assert updater.snapshot.get("totalEnergy") == 0


def test_a_storage_failure_does_not_block_the_updater(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken(_FakeStore):
        async def async_load(self) -> Any:
            raise OSError("disk")

    monkeypatch.setattr(common_network, "Store", Broken)
    updater = _updater(monkeypatch, _Charger(_boot_zero()))
    asyncio.run(updater.async_load_meter_baseline())

    asyncio.run(updater._async_update_data())

    assert updater.snapshot.get("totalEnergy") == 0


def test_the_readings_are_saved_when_they_change_not_on_every_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(common_network.time, "monotonic", lambda: clock[0])
    meters = {"totalEnergy": 10.0, "IEM1": 1.0, "IEM2": 2.0, "IEM1_money": 3.0, "IEM2_money": 4.0}
    charger = _Charger({**_classic_main(), **meters})
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater.async_load_meter_baseline())
    store = _FakeStore.instances[0]

    asyncio.run(updater._async_update_data())
    assert store.saves == [meters]

    # Unchanged readings, and changed ones inside the interval, write nothing.
    asyncio.run(updater._async_update_data())
    charger.main = {**charger.main, "totalEnergy": 10.5}
    clock[0] += METER_SAVE_INTERVAL_SECONDS - 1
    asyncio.run(updater._async_update_data())
    assert len(store.saves) == 1

    clock[0] += 1
    asyncio.run(updater._async_update_data())
    assert store.saves[-1] == {**meters, "totalEnergy": 10.5}
    assert len(store.saves) == 2


def test_the_latest_readings_are_saved_when_the_updater_shuts_down(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger({**_classic_main(), "totalEnergy": 10.0})
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater.async_load_meter_baseline())
    store = _FakeStore.instances[0]
    asyncio.run(updater._async_update_data())
    charger.main = {**charger.main, "totalEnergy": 10.4}
    asyncio.run(updater._async_update_data())
    assert store.saves[-1]["totalEnergy"] == 10.0

    asyncio.run(updater.async_shutdown())

    assert store.saves[-1]["totalEnergy"] == 10.4


def test_a_poll_finishing_during_unload_schedules_no_delayed_save(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the unload's own save may write: a delayed one could land after
    removing the entry deleted the file."""
    clock = [1000.0]
    monkeypatch.setattr(common_network.time, "monotonic", lambda: clock[0])
    charger = _Charger({**_classic_main(), "totalEnergy": 10.0})
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater.async_load_meter_baseline())
    store = _FakeStore.instances[0]
    asyncio.run(updater._async_update_data())

    updater._shutting_down = True
    charger.main = {**charger.main, "totalEnergy": 10.4}
    clock[0] += METER_SAVE_INTERVAL_SECONDS
    asyncio.run(updater._async_update_data())

    assert [save["totalEnergy"] for save in store.saves] == [10.0]


def test_a_failed_save_does_not_stop_the_unload(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken(_FakeStore):
        async def async_save(self, data: Any) -> None:
            raise OSError("disk")

    monkeypatch.setattr(common_network, "Store", Broken)
    updater = _updater(monkeypatch, _Charger({**_classic_main(), "totalEnergy": 10.0}))
    asyncio.run(updater.async_load_meter_baseline())
    asyncio.run(updater._async_update_data())

    asyncio.run(updater.async_shutdown())

    assert updater._shutting_down


def test_unloading_before_any_reading_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    updater = _updater(monkeypatch, _Charger(_boot_zero()))
    asyncio.run(updater.async_load_meter_baseline())

    asyncio.run(updater.async_shutdown())

    assert _FakeStore.instances[0].saves == []


@pytest.mark.parametrize(
    "saved",
    [None, [], "x", {"totalEnergy": "5"}, {"totalEnergy": True}, {"totalEnergy": -1.0},
     {"totalEnergy": math.nan}, {"totalEnergy": math.inf}, {"unknown": 9.0}],
)
def test_what_storage_hands_back_is_checked(saved: Any) -> None:
    guard = MeterGuard()
    guard.seed(saved)
    assert guard.baseline() == {}
    assert guard.apply({"totalEnergy": 0}) == {"totalEnergy": 0}


def test_a_seed_never_replaces_a_reading_already_accepted() -> None:
    guard = MeterGuard()
    guard.apply({"totalEnergy": 7.0})
    guard.seed({"totalEnergy": 5440.0, "IEM1": 3.0})
    assert guard.baseline() == {"totalEnergy": 7.0, "IEM1": 3.0}


def test_the_readings_live_in_one_store_per_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    updater = _updater(monkeypatch, _Charger(_boot_zero()))
    asyncio.run(updater.async_load_meter_baseline())
    (store,) = _FakeStore.instances
    assert (store.hass, store.version, store.key) == (updater.hass, 1, "eveus_meters_entry-1")


def test_readings_that_match_the_saved_ones_are_not_written_again(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeStore.loaded = dict(_SAVED)
    updater = _updater(monkeypatch, _Charger({**_classic_main(), **_SAVED}))
    asyncio.run(updater.async_load_meter_baseline())
    asyncio.run(updater._async_update_data())
    asyncio.run(updater._async_update_data())
    (store,) = _FakeStore.instances
    assert store.saves == []


def test_a_saved_zero_is_a_reading_like_any_other() -> None:
    guard = MeterGuard()
    guard.seed({"IEM1_money": 0, "IEM1": 0.5})
    assert guard.baseline() == {"IEM1_money": 0, "IEM1": 0.5}
