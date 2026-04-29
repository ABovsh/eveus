"""Unit tests for EV helper sensor edge cases."""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from custom_components.eveus import ev_sensors
from custom_components.eveus.ev_sensors import (
    BaseEVHelperSensor,
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


def test_missing_optional_soc_helpers_are_quiet_at_normal_log_levels(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calculator = CachedSOCCalculator()

    with caplog.at_level(logging.INFO, logger="custom_components.eveus.ev_sensors"):
        assert calculator.are_helpers_available(_Hass({})) is False

    assert caplog.records == []


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


def test_soc_energy_uses_real_zero_value_instead_of_stale_cache() -> None:
    sensor = EVSocKwhSensor(_Updater({"IEM1": "0"}))
    sensor._cached_data = {"IEM1": "12"}

    assert sensor._get_energy_charged() == 0


def test_time_to_target_uses_zero_power_instead_of_stale_cache() -> None:
    sensor = TimeToTargetSocSensor(_Updater({"IEM1": "16", "powerMeas": "0"}))
    sensor.hass = _Hass(HELPERS)
    sensor._cached_data = {"powerMeas": "7000"}

    assert sensor._get_sensor_value() == "Not charging"


def test_helper_sensors_track_inputs_even_when_helpers_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracked: list[tuple[str, ...]] = []

    async def noop_added_to_hass(self):
        return None

    def fake_track_state_change_event(hass, entity_ids, action):
        tracked.append(tuple(entity_ids))
        return lambda: None

    sensor = EVSocKwhSensor(_Updater({}))
    sensor.hass = _Hass({})
    monkeypatch.setattr(
        ev_sensors.EveusSensorBase,
        "async_added_to_hass",
        noop_added_to_hass,
    )
    monkeypatch.setattr(
        ev_sensors,
        "async_track_state_change_event",
        fake_track_state_change_event,
    )

    asyncio.run(BaseEVHelperSensor.async_added_to_hass(sensor))

    assert tracked == [sensor._tracked_inputs]


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
