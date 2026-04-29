"""Unit tests for optional EV helper sensors."""
from __future__ import annotations

from types import SimpleNamespace

from custom_components.eveus.ev_sensors import (
    CachedSOCCalculator,
    EVSocKwhSensor,
    TimeToTargetSocSensor,
)


class _States:
    def __init__(self, values: dict[str, object]) -> None:
        self._values = values

    def get(self, entity_id: str) -> SimpleNamespace | None:
        value = self._values.get(entity_id)
        if value is None:
            return None
        return SimpleNamespace(state=str(value))


class _Hass:
    def __init__(self, values: dict[str, object]) -> None:
        self.states = _States(values)


class _Updater:
    host = "192.168.1.50"
    available = True
    last_update_success = True

    def __init__(self, data: dict[str, object]) -> None:
        self.data = data

    def async_add_listener(self, *args: object, **kwargs: object):
        return lambda: None


HELPERS = {
    "input_number.ev_initial_soc": 20,
    "input_number.ev_battery_capacity": 80,
    "input_number.ev_soc_correction": 10,
    "input_number.ev_target_soc": 80,
}


def test_cached_soc_calculator_uses_shared_soc_math() -> None:
    calculator = CachedSOCCalculator()

    assert calculator.get_soc_kwh(_Hass(HELPERS), 20) == 34


def test_ev_sensors_keep_soc_calculator_per_instance() -> None:
    first_calculator = CachedSOCCalculator()
    second_calculator = CachedSOCCalculator()
    first = EVSocKwhSensor(_Updater({"IEM1": "10"}), 1, first_calculator)
    second = EVSocKwhSensor(_Updater({"IEM1": "10"}), 2, second_calculator)

    assert first._soc_calculator is first_calculator
    assert second._soc_calculator is second_calculator
    assert first._soc_calculator is not second._soc_calculator


def test_time_to_target_soc_uses_shared_calculator_cache() -> None:
    calculator = CachedSOCCalculator()
    sensor = TimeToTargetSocSensor(
        _Updater({"IEM1": "16", "powerMeas": "7000"}),
        1,
        calculator,
    )
    sensor.hass = _Hass(HELPERS)

    assert sensor._get_sensor_value() == "5h 20m"
    assert calculator.battery_capacity == 80
    assert calculator.target_soc == 80
