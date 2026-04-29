"""Unit tests for EV helper sensor edge cases."""
from __future__ import annotations

from types import SimpleNamespace

from custom_components.eveus.ev_sensors import (
    CachedSOCCalculator,
    EVSocKwhSensor,
    EVSocPercentSensor,
    InputEntitiesStatusSensor,
    TimeToTargetSocSensor,
)


class _States:
    def __init__(self, values: dict[str, object]) -> None:
        self._values = values

    def get(self, entity_id: str) -> SimpleNamespace | None:
        if entity_id not in self._values:
            return None
        return SimpleNamespace(state=str(self._values[entity_id]))


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


def test_soc_calculator_reports_missing_and_invalid_helpers() -> None:
    calculator = CachedSOCCalculator()

    assert calculator.are_helpers_available(_Hass({})) is False

    calculator.invalidate_cache()
    invalid = dict(HELPERS)
    invalid["input_number.ev_initial_soc"] = "bad"
    assert calculator.are_helpers_available(_Hass(invalid)) is False


def test_soc_calculator_percent_and_properties() -> None:
    calculator = CachedSOCCalculator()
    hass = _Hass(HELPERS)

    assert calculator.get_soc_percent(hass, 16) == 38
    assert calculator.battery_capacity == 80
    assert calculator.soc_correction == 10
    assert calculator.target_soc == 80


def test_soc_sensors_return_values_and_cache_last_valid_value() -> None:
    calculator = CachedSOCCalculator()
    hass = _Hass(HELPERS)
    updater = _Updater({"IEM1": "16"})

    kwh = EVSocKwhSensor(updater, 1, calculator)
    percent = EVSocPercentSensor(updater, 1, calculator)
    kwh.hass = hass
    percent.hass = hass

    assert kwh._get_sensor_value() == 30.4
    assert percent._get_sensor_value() == 38

    updater.data = {}
    assert kwh._get_sensor_value() == 16.0


def test_time_to_target_returns_helper_required_for_missing_helpers() -> None:
    sensor = TimeToTargetSocSensor(_Updater({"IEM1": "16", "powerMeas": "7000"}))
    sensor.hass = _Hass({})

    assert sensor._get_sensor_value() == "Helpers Required"


def test_input_entities_status_sensor_reports_missing_invalid_and_ready() -> None:
    sensor = InputEntitiesStatusSensor(_Updater({}))
    sensor.hass = _Hass({})

    assert sensor._get_sensor_value() == "Optional - 4 Missing"
    attrs = sensor.extra_state_attributes
    assert attrs["missing_count"] == 4
    assert "configuration_help" in attrs

    invalid = dict(HELPERS)
    invalid["input_number.ev_target_soc"] = "bad"
    sensor.hass = _Hass(invalid)
    sensor._last_check_time = 0
    assert sensor._get_sensor_value() == "Invalid 1 Inputs"

    sensor.hass = _Hass(HELPERS)
    sensor._last_check_time = 0
    assert sensor._get_sensor_value() == "All Present"
