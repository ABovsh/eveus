"""Hardening tests for rc.3: bool guard, NaN, negative values, MONETARY."""
from __future__ import annotations

import math

from homeassistant.components.sensor import SensorDeviceClass

from custom_components.eveus.utils import get_safe_value
from custom_components.eveus.sensor_definitions import (
    get_voltage,
    get_current,
    get_power,
    get_current_set,
    get_session_energy,
    get_leak_current,
    get_connection_quality,
    get_sensor_specifications,
)
from custom_components.eveus import ev_sensors


class _Updater:
    def __init__(self, data, quality=None):
        self.data = data
        self.available = True
        self.connection_quality = quality or {"success_rate": 100.0}


def test_get_safe_value_rejects_bool_for_numeric():
    assert get_safe_value({"x": True}, "x", float, default=None) is None
    assert get_safe_value({"x": False}, "x", int, default=None) is None
    # but real numbers still work
    assert get_safe_value({"x": "1.5"}, "x", float) == 1.5


def test_value_getter_rejects_bool():
    assert get_voltage(_Updater({"voltMeas1": True}), None) is None


def test_negative_voltage_returns_none():
    assert get_voltage(_Updater({"voltMeas1": -5}), None) is None
    assert get_voltage(_Updater({"voltMeas1": 230}), None) == 230


def test_negative_current_returns_none():
    assert get_current(_Updater({"curMeas1": -1.2}), None) is None


def test_negative_power_returns_none():
    assert get_power(_Updater({"powerMeas": -10}), None) is None


def test_current_set_below_minimum_returns_none():
    assert get_current_set(_Updater({"currentSet": 5}), None) is None
    assert get_current_set(_Updater({"currentSet": 7}), None) == 7
    assert get_current_set(_Updater({"currentSet": 16}), None) == 16


def test_leak_current_negative_returns_none():
    assert get_leak_current(_Updater({"leakValue": -3}), None) is None


def test_session_energy_negative_returns_none():
    assert get_session_energy(_Updater({"sessionEnergy": -0.5}), None) is None


def test_connection_quality_nan_returns_none():
    assert get_connection_quality(_Updater({}, quality={"success_rate": float("nan")}), None) is None
    assert get_connection_quality(_Updater({}, quality={"success_rate": float("inf")}), None) is None


def test_connection_quality_bool_returns_none():
    assert get_connection_quality(_Updater({}, quality={"success_rate": True}), None) is None


def test_connection_quality_valid_clamped():
    assert get_connection_quality(_Updater({}, quality={"success_rate": 150}), None) == 100
    assert get_connection_quality(_Updater({}, quality={"success_rate": -5}), None) == 0
    assert get_connection_quality(_Updater({}, quality={"success_rate": 87.4}), None) == 87


def test_counter_cost_sensors_have_monetary_device_class():
    by_key = {s.key: s for s in get_sensor_specifications(1)}
    for key in ("counter_a_cost", "counter_b_cost"):
        spec = by_key[key]
        assert spec.device_class == SensorDeviceClass.MONETARY
        assert spec.unit == "UAH"


class _SessionEnergyHolder:
    def __init__(self, value):
        self._updater = type("U", (), {"data": {"sessionEnergy": value}})()


def test_ev_energy_charged_rejects_negative():
    obj = _SessionEnergyHolder(-1.0)
    assert ev_sensors.BaseEVHelperSensor._get_energy_charged(obj) is None
    obj2 = _SessionEnergyHolder(3.5)
    assert ev_sensors.BaseEVHelperSensor._get_energy_charged(obj2) == 3.5
