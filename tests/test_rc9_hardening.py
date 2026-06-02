"""Hardening tests for 4.9.2-rc9.

Covers:
  * HA device_class/state_class contract validity (energy + monetary sensors)
  * SOC Energy as stored-energy (ENERGY_STORAGE)
  * Monetary cost sensors track meter resets via last_reset (correct TOTAL stats)
  * Current Set diagnostic sensor bounded by the configured model's maximum
  * connection_quality is not "healthy" before the first successful poll
  * diagnostics redact unknown sensitive-looking firmware fields
  * Time to Target SOC distinguishes "missing target helper" from "no helpers"
  * EveusUpdater exposes a public basic_auth accessor (no private reach-in)
  * calculate_soc_percent derives from the same kWh figure (single validation)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.components.sensor.const import DEVICE_CLASS_STATE_CLASSES

from conftest import (
    EV_HELPERS,
    EveusTestUpdater,
    HelperHass,
    TEST_HOST,
    TEST_PASSWORD,
    TEST_USERNAME,
    disable_state_writes,
)
from custom_components.eveus import diagnostics as diag
from custom_components.eveus import sensor_definitions as sd
from custom_components.eveus.common_network import EveusUpdater
from custom_components.eveus.ev_sensors import (
    CachedSOCCalculator,
    EVSocKwhSensor,
    TimeToTargetSocSensor,
)
from custom_components.eveus.sensor_definitions import create_sensor_specifications
from custom_components.eveus.utils import calculate_soc_kwh, calculate_soc_percent


class _Hass:
    loop = None


def _spec(key: str, **kwargs):
    """Return the SensorSpec with the given key from a fresh spec set."""
    for spec in create_sensor_specifications(**kwargs):
        if spec.key == key:
            return spec
    raise AssertionError(f"spec {key!r} not found")


# ---------------------------------------------------------------------------
# D0 — device_class / state_class contract validity
# ---------------------------------------------------------------------------

def test_all_sensor_specs_have_valid_device_and_state_class_pairs() -> None:
    """No spec may declare a state_class HA considers impossible for its device class."""
    for phases in (1, 3):
        for spec in create_sensor_specifications(phases=phases):
            if spec.device_class is None or spec.state_class is None:
                continue
            allowed = DEVICE_CLASS_STATE_CLASSES.get(spec.device_class)
            if allowed is None:
                continue
            assert spec.state_class in allowed, (
                f"{spec.key}: state_class {spec.state_class!r} is invalid for "
                f"device_class {spec.device_class!r} (allowed: {allowed})"
            )


def test_soc_energy_uses_energy_storage_device_class() -> None:
    """SOC Energy is a stored-battery-energy level, not a cumulative meter."""
    entity = EVSocKwhSensor(EveusTestUpdater(data={}), 1, CachedSOCCalculator())
    assert entity.device_class == SensorDeviceClass.ENERGY_STORAGE
    assert entity.state_class == SensorStateClass.MEASUREMENT
    allowed = DEVICE_CLASS_STATE_CLASSES.get(SensorDeviceClass.ENERGY_STORAGE)
    assert entity.state_class in allowed


# ---------------------------------------------------------------------------
# D1 — monetary cost sensors track resets via last_reset
# ---------------------------------------------------------------------------

class _Clock:
    """Deterministic clock; advances one minute per call."""

    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    def utcnow(self) -> datetime:
        self.now += timedelta(minutes=1)
        return self.now


def _cost_sensor(updater) -> object:
    entity = _spec("session_cost").create_sensor(updater, 1)
    disable_state_writes(entity)
    entity.hass = None
    return entity


def test_cost_sensor_sets_last_reset_on_first_value(monkeypatch) -> None:
    monkeypatch.setattr(sd.dt_util, "utcnow", _Clock().utcnow)
    updater = EveusTestUpdater(data={"sessionMoney": 4.32})
    entity = _cost_sensor(updater)

    entity._update_native_value()

    assert entity.native_value == 4.32
    assert entity.last_reset is not None


def test_cost_sensor_keeps_last_reset_while_value_rises(monkeypatch) -> None:
    monkeypatch.setattr(sd.dt_util, "utcnow", _Clock().utcnow)
    updater = EveusTestUpdater(data={"sessionMoney": 4.32})
    entity = _cost_sensor(updater)
    entity._update_native_value()
    first_reset = entity.last_reset

    updater.data = {"sessionMoney": 6.50}
    entity._update_native_value()

    assert entity.native_value == 6.50
    assert entity.last_reset == first_reset


def test_cost_sensor_advances_last_reset_when_meter_resets(monkeypatch) -> None:
    monkeypatch.setattr(sd.dt_util, "utcnow", _Clock().utcnow)
    updater = EveusTestUpdater(data={"sessionMoney": 6.50})
    entity = _cost_sensor(updater)
    entity._update_native_value()
    first_reset = entity.last_reset

    # New session: the charger resets sessionMoney to a smaller value.
    updater.data = {"sessionMoney": 1.10}
    entity._update_native_value()

    assert entity.native_value == 1.10
    assert entity.last_reset is not None
    assert entity.last_reset > first_reset


def test_cost_sensor_does_not_treat_offline_gap_as_reset(monkeypatch) -> None:
    monkeypatch.setattr(sd.dt_util, "utcnow", _Clock().utcnow)
    updater = EveusTestUpdater(data={"sessionMoney": 4.00})
    entity = _cost_sensor(updater)
    entity._update_native_value()
    first_reset = entity.last_reset

    # Charger drops offline → value None; must not count as a reset.
    updater.data = {}
    entity._update_native_value()
    # Comes back with a HIGHER value (meter kept counting): still same window.
    updater.data = {"sessionMoney": 4.80}
    entity._update_native_value()

    assert entity.last_reset == first_reset


# ---------------------------------------------------------------------------
# D2 — Current Set diagnostic sensor bounded by the configured model maximum
# ---------------------------------------------------------------------------

def test_current_set_sensor_rejects_value_above_model_maximum() -> None:
    spec = _spec("current_set", phases=1, max_current=16)
    updater = EveusTestUpdater(data={"currentSet": 40})  # impossible on a 16 A unit
    entity = spec.create_sensor(updater, 1)
    disable_state_writes(entity)
    entity.hass = None

    assert entity._get_sensor_value() is None


def test_current_set_sensor_accepts_value_within_model_maximum() -> None:
    spec = _spec("current_set", phases=1, max_current=16)
    updater = EveusTestUpdater(data={"currentSet": 14})
    entity = spec.create_sensor(updater, 1)
    disable_state_writes(entity)
    entity.hass = None

    assert entity._get_sensor_value() == 14


# ---------------------------------------------------------------------------
# D3 — connection_quality is not "healthy" before any successful poll
# ---------------------------------------------------------------------------

def test_connection_quality_not_healthy_before_first_success() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    assert updater.connection_quality["is_healthy"] is False


def test_connection_quality_healthy_after_success() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._record_success(0.1, {"state": 2, "currentSet": 16})
    assert updater.connection_quality["is_healthy"] is True


# ---------------------------------------------------------------------------
# I1 — diagnostics redact unknown sensitive-looking firmware fields
# ---------------------------------------------------------------------------

def test_sensitive_keys_flags_identifying_fields_only() -> None:
    data = {
        "state": 2,
        "currentSet": 16,
        "powerMeas": 1500,
        "sessionEnergy": 4.2,
        "IEM1_money": 10,
        "tarifAValue": 432,
        "wifiSSID": "MyHomeNet",  # NOSONAR - test fixture, not a real SSID
        "MACaddr": "aa:bb:cc:dd:ee:ff",  # NOSONAR - test fixture
        "STA_IP_Addres": "10.0.0.5",  # NOSONAR(python:S1313) - test fixture LAN
        "deviceToken": "abc123",  # NOSONAR - test fixture token
    }
    flagged = diag._sensitive_keys(data)

    assert {"wifiSSID", "MACaddr", "STA_IP_Addres", "deviceToken"} <= flagged
    assert flagged.isdisjoint(
        {"state", "currentSet", "powerMeas", "sessionEnergy", "IEM1_money", "tarifAValue"}
    )


# ---------------------------------------------------------------------------
# I2 — Time to Target SOC: distinguish "set a target" from "no helpers"
# ---------------------------------------------------------------------------

def test_time_to_target_prompts_for_target_when_only_target_missing() -> None:
    # Core SOC values pushed, but no target_soc.
    calc = CachedSOCCalculator()
    calc.set_value("initial_soc", EV_HELPERS["input_number.ev_initial_soc"])
    calc.set_value("battery_capacity", EV_HELPERS["input_number.ev_battery_capacity"])
    calc.set_value("soc_correction", EV_HELPERS["input_number.ev_soc_correction"])
    updater = EveusTestUpdater(data={"powerMeas": 5000, "sessionEnergy": 10})
    sensor = TimeToTargetSocSensor(updater, 1, calc)
    disable_state_writes(sensor)

    assert sensor._get_sensor_value() == "Set Target SOC"


def test_time_to_target_reports_helpers_required_when_no_helpers() -> None:
    updater = EveusTestUpdater(data={"powerMeas": 5000, "sessionEnergy": 10})
    sensor = TimeToTargetSocSensor(updater, 1, CachedSOCCalculator())
    sensor.hass = HelperHass({})
    disable_state_writes(sensor)

    assert sensor._get_sensor_value() == "Helpers Required"


# ---------------------------------------------------------------------------
# I3 — public basic_auth accessor (decouple CommandManager from internals)
# ---------------------------------------------------------------------------

def test_updater_exposes_basic_auth_accessor() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    assert updater.basic_auth is updater._basic_auth


# ---------------------------------------------------------------------------
# O1 — calculate_soc_percent derives from the same rounded kWh figure
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "initial,capacity,energy,loss",
    [
        (20, 80, 16, 10),
        (0, 60, 0, 7.5),
        (50, 100, 25, 0),
        (90, 40, 30, 5),
    ],
)
def test_soc_percent_matches_kwh_derivation(initial, capacity, energy, loss) -> None:
    kwh = calculate_soc_kwh(initial, capacity, energy, loss)
    expected = round(max(0, min(kwh / capacity * 100, 100)), 0)
    assert calculate_soc_percent(initial, capacity, energy, loss) == expected
