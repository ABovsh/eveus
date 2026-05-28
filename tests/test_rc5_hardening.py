"""Hardening tests for 4.9.2-rc5: firmware-domain guards, numeric safety, UX."""
from __future__ import annotations

import asyncio
import math
from datetime import timedelta
from types import SimpleNamespace

import pytest
from homeassistant.components.number import NumberEntityDescription
from homeassistant.exceptions import HomeAssistantError

from conftest import EveusTestUpdater, TEST_HOST, TEST_PASSWORD, TEST_USERNAME
from custom_components.eveus import binary_sensor as bs
from custom_components.eveus import common_network, number as number_mod
from custom_components.eveus import sensor_definitions as sd
from custom_components.eveus.common_network import EveusUpdater


# ---------------------------------------------------------------------------
# F01 / F03 — coordinator hardens against out-of-domain `state`
# ---------------------------------------------------------------------------

def test_invalid_state_keeps_offline_cadence() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, object())
    updater._tune_update_interval({"state": 99})
    assert updater.update_interval == timedelta(seconds=common_network.OFFLINE_UPDATE_INTERVAL)


def test_known_state_picks_idle_cadence() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, object())
    updater._tune_update_interval({"state": 3})  # Connected
    assert updater.update_interval == timedelta(seconds=common_network.IDLE_UPDATE_INTERVAL)


# ---------------------------------------------------------------------------
# F04 — Car Connected returns None for unknown future state codes
# ---------------------------------------------------------------------------

def test_car_connected_unknown_state_returns_none() -> None:
    updater = EveusTestUpdater({"state": 99})
    sensor = bs.EveusCarConnectedBinarySensor(updater, 1)
    assert sensor.is_on is None


def test_car_connected_charging_state_returns_true() -> None:
    updater = EveusTestUpdater({"state": 4})
    sensor = bs.EveusCarConnectedBinarySensor(updater, 1)
    assert sensor.is_on is True


# ---------------------------------------------------------------------------
# New: Session Active binary sensor
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "state,expected",
    [(0, False), (2, False), (3, False), (4, True), (5, False), (6, True), (99, None)],
)
def test_session_active_mapping(state: int, expected: bool | None) -> None:
    updater = EveusTestUpdater({"state": state})
    sensor = bs.EveusSessionActiveBinarySensor(updater, 1)
    assert sensor.is_on is expected


# ---------------------------------------------------------------------------
# F05 — Substate returns None when state itself is invalid
# ---------------------------------------------------------------------------

def test_substate_returns_none_for_unknown_state() -> None:
    updater = EveusTestUpdater({"state": 99, "subState": 1})
    assert sd.get_charger_substate(updater, None) is None


# ---------------------------------------------------------------------------
# F06 — Switch ignores firmware values outside {0, 1}
# ---------------------------------------------------------------------------

def test_switch_rejects_out_of_domain_state_value() -> None:
    from custom_components.eveus.switch import (
        SWITCH_DESCRIPTIONS,
        BaseSwitchEntity,
    )

    description = SWITCH_DESCRIPTIONS[0]  # Stop Charging / evseEnabled
    updater = EveusTestUpdater({"evseEnabled": 2})
    sw = BaseSwitchEntity(updater, description, 1)
    # 2 is outside the 0/1 domain — _resolve_state falls through to default False
    assert sw._resolve_state() is False


# ---------------------------------------------------------------------------
# F07 — Energy Limit Reached returns None for out-of-domain value
# ---------------------------------------------------------------------------

def test_limit_reached_rejects_non_binary_value() -> None:
    updater = EveusTestUpdater({"energyLimitS": 2})
    sensor = bs.EveusLimitReachedBinarySensor(
        updater, 1, "Energy Limit Reached", "energyLimitS", "mdi:flash-alert"
    )
    assert sensor.is_on is None


# ---------------------------------------------------------------------------
# F08 / F10 — Number entity hides device values outside declared range
# ---------------------------------------------------------------------------

def test_charging_current_hides_out_of_range_device_value() -> None:
    updater = EveusTestUpdater({"currentSet": 48})  # 16A model max
    num = number_mod.EveusCurrentNumber(updater, "16A", 1)
    assert num._resolve_value() is None


def test_energy_limit_hides_out_of_range_device_value() -> None:
    description, command, max_v = number_mod.SESSION_LIMIT_DESCRIPTIONS[0]
    updater = EveusTestUpdater({command: max_v + 100})
    num = number_mod.EveusSessionLimitNumber(updater, description, command, max_v, 1)
    assert num._resolve_value() is None


# ---------------------------------------------------------------------------
# F09 / F11 — Setter validation helper rejects NaN/inf/bool before clamping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), True, False])
def test_validate_finite_number_rejects_bad_input(bad) -> None:
    with pytest.raises(HomeAssistantError):
        number_mod._validate_finite_number(bad, "Charging Current")


@pytest.mark.parametrize("good", [7, 16.0, 100])
def test_validate_finite_number_accepts_normal_input(good) -> None:
    assert number_mod._validate_finite_number(good, "Limit") == float(good)


# ---------------------------------------------------------------------------
# F12 — Active Rate Cost rejects negative tariff
# ---------------------------------------------------------------------------

def test_active_rate_cost_rejects_negative_tariff() -> None:
    updater = EveusTestUpdater({"activeTarif": 0, "tarif": -100})
    assert sd.get_active_rate_cost(updater, None) is None


def test_active_rate_cost_returns_value_when_positive() -> None:
    updater = EveusTestUpdater({"activeTarif": 1, "tarifAValue": 250})
    assert sd.get_active_rate_cost(updater, None) == 2.5


# ---------------------------------------------------------------------------
# F13 — Schedule attrs drop invalid current/energy
# ---------------------------------------------------------------------------

def test_schedule_attrs_drop_invalid_current_and_energy() -> None:
    updater = EveusTestUpdater(
        {
            "sh1Start": 60,
            "sh1Stop": 120,
            "sh1CurrentEnable": 1,
            "sh1CurrentValue": -5,
            "sh1EnergyEnable": 1,
            "sh1EnergyValue": -1.0,
        }
    )
    attrs = sd._make_schedule_attrs(1)(updater, None)
    assert "current_limit_a" not in attrs
    assert "energy_limit_kwh" not in attrs


# ---------------------------------------------------------------------------
# F14 / F15 — RSSI and Battery Voltage domain guards
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [10, 50, 100])
def test_wifi_rssi_rejects_positive_values(bad: int) -> None:
    assert sd.get_wifi_rssi(EveusTestUpdater({"RSSI": bad}), None) is None


def test_wifi_rssi_accepts_typical_range() -> None:
    assert sd.get_wifi_rssi(EveusTestUpdater({"RSSI": -55}), None) == -55


def test_battery_voltage_rejects_negative() -> None:
    assert sd.get_battery_voltage(EveusTestUpdater({"vBat": -2.5}), None) is None


# ---------------------------------------------------------------------------
# F18 — Plaintext warning fires on reconfigure
# ---------------------------------------------------------------------------

def test_warn_if_plaintext_emits_for_http(caplog) -> None:
    from custom_components.eveus import config_flow

    with caplog.at_level("WARNING"):
        config_flow._warn_if_plaintext("http")
    assert any("plaintext" in r.getMessage().lower() or "http" in r.getMessage().lower()
               for r in caplog.records)


# ---------------------------------------------------------------------------
# F21 — Permanent HTTP errors are not retried
# ---------------------------------------------------------------------------

def test_command_retry_skips_permanent_4xx_source_check() -> None:
    """The retry loop must filter ClientResponseError by status code.

    A live integration test through `send_command` is fragile to mock; instead
    assert the implementation references the transient-status filter so future
    refactors don't silently revert to "retry every 4xx".
    """
    import inspect
    from custom_components.eveus import common_command

    src = inspect.getsource(common_command)
    # Permanent client errors are not retried; only transient/server statuses are.
    assert "(408, 425, 429, 500, 502, 503, 504)" in src


# ---------------------------------------------------------------------------
# Removed entities should be gone from the registries
# ---------------------------------------------------------------------------

def test_removed_entities_not_in_number_specs() -> None:
    keys = {d.key for d, _c, _m in number_mod.SESSION_LIMIT_DESCRIPTIONS}
    assert "time_limit" not in keys
    assert "money_limit" not in keys
    assert "energy_limit" in keys


def test_removed_entities_not_in_binary_specs() -> None:
    names = {spec[0] for spec in bs._LIMIT_REACHED_SPECS}
    assert "Time Limit Reached" not in names
    assert "Money Limit Reached" not in names
    assert "Energy Limit Reached" in names


def test_control_pilot_removed_from_sensor_specs() -> None:
    specs = sd.create_sensor_specifications()
    names = {s.name for s in specs}
    assert "Control Pilot" not in names


# ---------------------------------------------------------------------------
# F17 — Time to Target SOC invalidates cached ETA when helpers disappear
# ---------------------------------------------------------------------------

def test_time_to_target_resets_cache_when_helpers_missing() -> None:
    from custom_components.eveus.ev_sensors import (
        CachedSOCCalculator,
        TimeToTargetSocSensor,
    )

    calc = CachedSOCCalculator()
    updater = EveusTestUpdater({"powerMeas": 3500, "sessionEnergy": 1.0})
    sensor = TimeToTargetSocSensor(updater, 1, calc)

    # Prime a stale cached ETA as if a previous tick had succeeded.
    sensor._cached_value = "2h 15m"

    # Helpers unavailable → must reset, not keep showing "2h 15m".
    assert sensor._get_sensor_value() == "Helpers Required"
    assert sensor._cached_value == "Helpers Required"
