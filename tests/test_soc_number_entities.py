"""Native SOC number entities."""
import asyncio

import pytest

from conftest import EveusTestUpdater, HelperHass, disable_state_writes
from custom_components.eveus import number as number_module
from custom_components.eveus.ev_sensors import CachedSOCCalculator
from custom_components.eveus.number import (
    EveusBatteryCapacityNumber,
    EveusInitialSocNumber,
    EveusSocCorrectionNumber,
    EveusTargetSocNumber,
    build_soc_numbers,
)


def _updater() -> EveusTestUpdater:
    """Build a fake updater exposing .config_entry.entry_id."""
    return EveusTestUpdater({})


def test_ranges_modes_and_defaults() -> None:
    updater = _updater()
    calc = CachedSOCCalculator()
    n_cap = EveusBatteryCapacityNumber(updater, calc, seed=64, device_number=1)
    assert (n_cap.native_min_value, n_cap.native_max_value) == (10, 160)
    assert n_cap.native_step == 1
    assert n_cap.mode in ("box",) or str(n_cap.mode) == "NumberMode.BOX"
    assert n_cap.native_value == 64

    n_init = EveusInitialSocNumber(updater, calc, seed=20, device_number=1)
    assert (n_init.native_min_value, n_init.native_max_value) == (0, 100)

    n_corr = EveusSocCorrectionNumber(updater, calc, seed=7.5, device_number=1)
    assert (n_corr.native_min_value, n_corr.native_max_value) == (0, 20)

    n_tgt = EveusTargetSocNumber(updater, calc, seed=80, device_number=1)
    assert n_tgt.native_step == 5


def test_set_value_pushes_to_calculator(monkeypatch) -> None:
    updater = _updater()
    calc = CachedSOCCalculator()
    n_cap = EveusBatteryCapacityNumber(updater, calc, seed=50, device_number=1)
    n_cap.hass = HelperHass({})
    disable_state_writes(n_cap)
    monkeypatch.setattr(number_module, "async_dispatcher_send", lambda *a, **k: None)

    asyncio.run(n_cap.async_set_native_value(70))
    assert calc.battery_capacity == 70
    assert n_cap.native_value == 70


def test_set_value_clamps_to_limits(monkeypatch) -> None:
    updater = _updater()
    calc = CachedSOCCalculator()
    n_cap = EveusBatteryCapacityNumber(updater, calc, seed=50, device_number=1)
    n_cap.hass = HelperHass({})
    disable_state_writes(n_cap)
    monkeypatch.setattr(number_module, "async_dispatcher_send", lambda *a, **k: None)

    asyncio.run(n_cap.async_set_native_value(9999))
    assert n_cap.native_value == 160
    asyncio.run(n_cap.async_set_native_value(-5))
    assert n_cap.native_value == 10


def test_distinct_unique_ids() -> None:
    updater = _updater()
    calc = CachedSOCCalculator()
    nums = build_soc_numbers(
        updater,
        calc,
        seeds={
            "initial_soc": 20,
            "target_soc": 80,
            "battery_capacity": 50,
            "soc_correction": 7.5,
        },
        device_number=1,
    )
    uids = {n.unique_id for n in nums}
    assert len(uids) == 4
    for n in nums:
        assert n.entity_category == "config" or str(n.entity_category) == "EntityCategory.CONFIG"


def test_build_soc_numbers_returns_four() -> None:
    updater = _updater()
    calc = CachedSOCCalculator()
    nums = build_soc_numbers(
        updater,
        calc,
        seeds={
            "initial_soc": 20,
            "target_soc": 80,
            "battery_capacity": 50,
            "soc_correction": 7.5,
        },
        device_number=1,
    )
    assert len(nums) == 4


def _run_added_to_hass(entity, restored_value, monkeypatch) -> None:
    """Drive async_added_to_hass with a stubbed restore source."""
    from types import SimpleNamespace

    async def noop_super(self):
        return None

    monkeypatch.setattr(
        number_module.BaseEveusEntity, "async_added_to_hass", noop_super
    )
    monkeypatch.setattr(number_module, "async_dispatcher_send", lambda *a, **k: None)

    async def fake_last(self):
        if restored_value is None:
            return None
        return SimpleNamespace(native_value=restored_value)

    monkeypatch.setattr(
        type(entity), "async_get_last_number_data", fake_last, raising=False
    )
    asyncio.run(number_module.EveusSocConfigNumber.async_added_to_hass(entity))


def test_restore_in_range_value_applied(monkeypatch) -> None:
    updater = _updater()
    calc = CachedSOCCalculator()
    n = EveusBatteryCapacityNumber(updater, calc, seed=50, device_number=1)
    n.hass = HelperHass({})
    _run_added_to_hass(n, 90, monkeypatch)
    assert n.native_value == 90
    assert calc.battery_capacity == 90


def test_restore_out_of_range_value_ignored_keeps_seed(monkeypatch) -> None:
    updater = _updater()
    calc = CachedSOCCalculator()
    n = EveusBatteryCapacityNumber(updater, calc, seed=50, device_number=1)
    n.hass = HelperHass({})
    _run_added_to_hass(n, 9999, monkeypatch)
    assert n.native_value == 50
    assert calc.battery_capacity == 50


def test_restore_none_keeps_seed_and_still_pushes(monkeypatch) -> None:
    updater = _updater()
    calc = CachedSOCCalculator()
    n = EveusBatteryCapacityNumber(updater, calc, seed=50, device_number=1)
    n.hass = HelperHass({})
    _run_added_to_hass(n, None, monkeypatch)
    assert n.native_value == 50
    assert calc.battery_capacity == 50


def test_build_soc_numbers_uses_defaults_when_seed_missing() -> None:
    updater = _updater()
    calc = CachedSOCCalculator()
    nums = build_soc_numbers(updater, calc, seeds={}, device_number=1)
    by_key = {n._soc_key: n for n in nums}
    assert by_key["initial_soc"].native_value == 20
    assert by_key["target_soc"].native_value == 80
    assert by_key["battery_capacity"].native_value == 50
    assert by_key["soc_correction"].native_value == 7.5


def test_seed_is_normalized_on_construction() -> None:
    """A bad seed is clamped/defaulted so it can't reach the calculator."""
    updater = _updater()
    calc = CachedSOCCalculator()

    n_high = EveusBatteryCapacityNumber(updater, calc, seed=999, device_number=1)
    assert n_high.native_value == 160  # clamped to max

    n_low = EveusBatteryCapacityNumber(updater, calc, seed=0, device_number=1)
    assert n_low.native_value == 10  # clamped to min

    n_nan = EveusBatteryCapacityNumber(
        updater, calc, seed=float("nan"), device_number=1
    )
    assert n_nan.native_value == 50  # default
