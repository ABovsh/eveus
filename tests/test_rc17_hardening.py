"""Regression tests for the 2026-06-16 deep-assessment findings (V-01..V-22)."""
from __future__ import annotations

import asyncio
import time
from datetime import timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.util import dt as dt_util

import custom_components.eveus.number as number_mod
import custom_components.eveus.sensor_definitions as sd
from custom_components.eveus.ev_sensors import CachedSOCCalculator
from custom_components.eveus.soc_limit import (
    EVENT_SOC_LIMIT_REACHED,
    SocLimitController,
)
from custom_components.eveus.utils import calculate_remaining_seconds


# =============================================================================
# V-02 — malformed/off-list minVoltage must not widen the undervoltage floor
# =============================================================================

def _threshold(data):
    updater = MagicMock()
    updater.available = True
    updater.data = data
    updater.send_command = AsyncMock(return_value=True)
    updater.config_entry = MagicMock()
    ent = number_mod.EveusUndervoltageThresholdNumber(
        updater, number_mod.UNDERVOLTAGE_THRESHOLD_NUMBER, device_number=1
    )
    ent.hass = MagicMock()
    ent.async_write_ha_state = MagicMock()
    return ent


def test_v02_negative_minvoltage_keeps_static_floor():
    ent = _threshold({"aiVoltage": 215, "minVoltage": -1000})
    assert ent.native_min_value == 210


def test_v02_offlist_minvoltage_keeps_static_floor():
    ent = _threshold({"aiVoltage": 215, "minVoltage": 190})
    assert ent.native_min_value == 210


def test_v02_supported_minvoltage_still_tracks():
    ent = _threshold({"aiVoltage": 215, "minVoltage": 180})
    assert ent.native_min_value == 190


# =============================================================================
# V-03 — SOC controller must stand down when suspendLimits is not a clean 0
# =============================================================================

def _calc(target=80, initial=20, cap=50, corr=0):
    c = CachedSOCCalculator()
    c.set_value("initial_soc", initial)
    c.set_value("battery_capacity", cap)
    c.set_value("soc_correction", corr)
    c.set_value("target_soc", target)
    return c


def _soc_updater(**data):
    u = MagicMock()
    u.available = True
    u.last_update_success = True
    u.device_number = 1
    base = {"state": 4, "sessionEnergy": 30.0, "evseEnabled": 0}
    base.update(data)
    u.data = base
    u.send_command = AsyncMock(return_value=True)
    return u


def _soc_ctrl(calc, updater):
    hass = MagicMock()
    hass.async_create_task = lambda coro: asyncio.run(coro)
    hass.bus.async_fire = MagicMock()
    return SocLimitController(hass, updater, calc)


@pytest.mark.parametrize("suspend", [None, "bad", 2, -1])
def test_v03_does_not_enforce_when_suspendlimits_unknown(suspend):
    data = {} if suspend is None else {"suspendLimits": suspend}
    updater = _soc_updater(**data)
    ctrl = _soc_ctrl(_calc(), updater)
    ctrl.set_enabled(True)
    ctrl.process()
    updater.send_command.assert_not_called()


def test_v03_enforces_only_when_suspendlimits_zero():
    updater = _soc_updater(suspendLimits=0)
    ctrl = _soc_ctrl(_calc(), updater)
    ctrl.set_enabled(True)
    ctrl.process()
    updater.send_command.assert_awaited_once_with("evseEnabled", 1)


# =============================================================================
# V-08 — tiny finite power must not yield an absurd ETA
# =============================================================================

def test_v08_tiny_power_returns_no_eta():
    assert (
        calculate_remaining_seconds(
            current_soc=50, target_soc=80, power_meas=1e-250,
            battery_capacity=50, correction=0,
        )
        is None
    )


def test_v08_normal_power_still_returns_eta():
    secs = calculate_remaining_seconds(
        current_soc=50, target_soc=80, power_meas=7000,
        battery_capacity=50, correction=0,
    )
    assert secs is not None and secs > 0


# =============================================================================
# V-09 — battery voltage sensor must reject implausible CR2032 readings
# =============================================================================

@pytest.mark.parametrize("bad", [0, 5.01, 12.5, 100, 500])
def test_v09_battery_voltage_rejects_implausible(bad):
    upd = SimpleNamespace(available=True, data={"vBat": bad})
    assert sd.get_battery_voltage(upd, None) is None


def test_v09_battery_voltage_accepts_plausible():
    upd = SimpleNamespace(available=True, data={"vBat": 3.0})
    assert sd.get_battery_voltage(upd, None) == 3.0


# =============================================================================
# V-10 — Time Drift must clear stale drift once the clock is back in sync
# =============================================================================

@pytest.fixture
def _tz_kyiv():
    original = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(timezone(timedelta(hours=3)))
    yield
    dt_util.set_default_time_zone(original)


def test_v10_drift_clears_after_sync(_tz_kyiv):
    shift = 3 * 3600
    updater = SimpleNamespace(
        available=True,
        data={"timeZone": "3", "systemTime": str(int(time.time()) + shift + 30)},
    )
    assert sd.get_time_drift(updater, None) == 30
    updater.data["systemTime"] = str(int(time.time()) + shift)
    assert sd.get_time_drift(updater, None) == 0


# =============================================================================
# V-11 — target-SOC logic must use exact SOC, not the rounded display percent
# =============================================================================

def test_v11_soc_limit_does_not_stop_before_exact_target():
    # 20% initial on 50 kWh = 10 kWh; +29.8 kWh = 39.8 kWh = 79.6% which ROUNDS
    # UP to 80 (== target) but is exactly below it -> must NOT stop early.
    calc = _calc(target=80, initial=20, cap=50, corr=0)
    updater = _soc_updater(suspendLimits=0, sessionEnergy=29.8)
    ctrl = _soc_ctrl(calc, updater)
    ctrl.set_enabled(True)
    ctrl.process()
    updater.send_command.assert_not_called()


def test_v11_calculator_exposes_exact_percent():
    calc = _calc(target=80, initial=20, cap=50, corr=0)
    # 10 kWh initial + 29.8 kWh = 39.8 kWh on 50 kWh = 79.6% exact
    exact = calc.get_soc_percent_exact(29.8)
    assert 79.5 < exact < 79.7
    # the displayed percent still rounds
    assert calc.get_soc_percent(29.8) == 80


# =============================================================================
# V-12 — Error state with subState 0 must not render "No Error"
# =============================================================================

def test_v12_error_state_zero_substate_is_unknown():
    upd = SimpleNamespace(available=True, data={"state": 7, "subState": 0})
    assert sd.get_charger_substate(upd, None) is None


def test_v12_normal_state_zero_substate_still_maps():
    upd = SimpleNamespace(available=True, data={"state": 2, "subState": 0})
    assert sd.get_charger_substate(upd, None) == "No Limits"


def test_v12_error_state_real_fault_still_maps():
    upd = SimpleNamespace(available=True, data={"state": 7, "subState": 10})
    assert sd.get_charger_substate(upd, None) == "Overcurrent"


# =============================================================================
# V-21 — schedule energy limit must round firmware float noise for display
# =============================================================================

def test_v21_schedule_energy_has_display_precision():
    for desc in number_mod.SCHEDULE_LIMIT_NUMBERS:
        if desc.key.endswith("energy_limit"):
            assert desc.display_precision == 3
