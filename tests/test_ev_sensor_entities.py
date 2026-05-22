"""Unit tests for EV helper sensor edge cases."""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from conftest import EV_HELPERS, EveusTestUpdater, HelperHass
from custom_components.eveus import ev_sensors
from custom_components.eveus.ev_sensors import (
    BaseEVHelperSensor,
    CachedSOCCalculator,
    ChargingFinishTimeSensor,
    EVSocKwhSensor,
    EVSocPercentSensor,
    InputEntitiesStatusSensor,
    TimeToTargetSocSensor,
)


def test_soc_calculator_reports_missing_and_invalid_helpers() -> None:
    calculator = CachedSOCCalculator()

    assert calculator.are_helpers_available(HelperHass({})) is False

    calculator.invalidate_cache()
    invalid = dict(EV_HELPERS)
    invalid["input_number.ev_initial_soc"] = "bad"
    assert calculator.are_helpers_available(HelperHass(invalid)) is False

    # An out-of-range REQUIRED helper disables SOC entirely.
    calculator.invalidate_cache()
    out_of_range = dict(EV_HELPERS)
    out_of_range["input_number.ev_battery_capacity"] = 999
    assert calculator.are_helpers_available(HelperHass(out_of_range)) is False

    # target_soc is OPTIONAL: out-of-range / missing must not disable SOC.
    # ETA sensors check `target_soc` separately and degrade gracefully.
    calculator.invalidate_cache()
    bad_target = dict(EV_HELPERS)
    bad_target["input_number.ev_target_soc"] = 150
    assert calculator.are_helpers_available(HelperHass(bad_target)) is True
    assert calculator.target_soc is None
    assert calculator.battery_capacity == 80

    calculator.invalidate_cache()
    no_target = {k: v for k, v in EV_HELPERS.items() if k != "input_number.ev_target_soc"}
    assert calculator.are_helpers_available(HelperHass(no_target)) is True
    assert calculator.target_soc is None
    assert calculator.get_soc_percent(HelperHass(no_target), 0) == 20  # Initial SOC fallback


def test_missing_optional_soc_helpers_are_quiet_at_normal_log_levels(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calculator = CachedSOCCalculator()

    with caplog.at_level(logging.INFO, logger="custom_components.eveus.ev_sensors"):
        assert calculator.are_helpers_available(HelperHass({})) is False

    assert caplog.records == []


def test_soc_calculator_percent_and_properties() -> None:
    calculator = CachedSOCCalculator()
    hass = HelperHass(EV_HELPERS)

    assert calculator.get_soc_percent(hass, 16) == 38
    assert calculator.battery_capacity == 80
    assert calculator.soc_correction == 10
    assert calculator.target_soc == 80


def test_soc_sensors_return_values_and_cache_last_valid_value() -> None:
    calculator = CachedSOCCalculator()
    hass = HelperHass(EV_HELPERS)
    updater = EveusTestUpdater({"sessionEnergy": "16"})

    kwh = EVSocKwhSensor(updater, 1, calculator)
    percent = EVSocPercentSensor(updater, 1, calculator)
    kwh.hass = hass
    percent.hass = hass

    # initial=20% × 80 + sessionEnergy=16 × (1-0.1) = 16 + 14.4 = 30.4 kWh → 38%
    assert kwh._get_sensor_value() == pytest.approx(30.4)
    assert percent._get_sensor_value() == 38

    updater.data = {"sessionEnergy": "20"}
    # 16 + 18 = 34 kWh → 42.5% (banker's-rounds to 42)
    assert kwh._get_sensor_value() == 34
    assert percent._get_sensor_value() == 42

    updater.data = {}
    # No sessionEnergy → treat as 0 delivered and reproject from Initial SOC.
    # 20% × 80 kWh = 16.0. Avoids the entity going "unknown" at cold start.
    assert kwh._get_sensor_value() == pytest.approx(16.0)
    assert percent._get_sensor_value() == 20


def test_soc_energy_uses_real_zero_value_instead_of_stale_cache() -> None:
    sensor = EVSocKwhSensor(EveusTestUpdater({"sessionEnergy": "0"}))

    assert sensor._get_energy_charged() == 0


def test_time_to_target_uses_zero_power_instead_of_stale_cache() -> None:
    sensor = TimeToTargetSocSensor(
        EveusTestUpdater({"sessionEnergy": "16", "powerMeas": "0"})
    )
    sensor.hass = HelperHass(EV_HELPERS)

    assert sensor._get_sensor_value() == "Not charging"


def test_helper_sensors_track_inputs_even_when_helpers_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracked: list[tuple[str, ...]] = []
    cleanup_callbacks: list[object] = []

    async def noop_added_to_hass(self):
        return None

    def fake_track_state_change_event(hass, entity_ids, action):
        tracked.append(tuple(entity_ids))
        return lambda: None

    sensor = EVSocKwhSensor(EveusTestUpdater({}))
    sensor.hass = HelperHass({})
    sensor.async_on_remove = lambda callback: cleanup_callbacks.append(callback)
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
    assert len(cleanup_callbacks) == 1


def test_helper_sensor_available_property_is_pure() -> None:
    sensor = EVSocKwhSensor(EveusTestUpdater({"sessionEnergy": "16"}))
    hass = HelperHass(EV_HELPERS)
    sensor.hass = hass
    sensor._helpers_available = True

    assert sensor.available is True
    assert hass.states.calls == []


def test_helper_sensor_coordinator_update_refreshes_helper_status() -> None:
    writes = 0
    sensor = EVSocKwhSensor(EveusTestUpdater({"sessionEnergy": "16"}))
    sensor.hass = HelperHass(EV_HELPERS)
    sensor.async_write_ha_state = lambda: None

    def record_write() -> None:
        nonlocal writes
        writes += 1

    sensor.async_write_ha_state = record_write

    assert sensor.available is False
    sensor._handle_coordinator_update()

    assert sensor.available is True
    # sessionEnergy=16, initial=20%, capacity=80, loss=10 → 16 + 14.4 = 30.4 kWh
    assert sensor.native_value == pytest.approx(30.4)
    assert writes == 1


def test_soc_reprojects_when_initial_soc_changes() -> None:
    """4.6.0: SoC = initial_soc% × capacity + sessionEnergy × efficiency.
    Mid-session correction of initial_soc reprojects on the next poll without
    any baseline machinery — there is nothing to invalidate."""
    values = dict(EV_HELPERS)
    hass = HelperHass(values)
    updater = EveusTestUpdater({"sessionEnergy": "16", "state": 4})
    sensor = EVSocKwhSensor(updater)
    sensor.hass = hass

    # 0.20×80 + 16×0.9 = 30.4 kWh
    assert sensor._get_sensor_value() == pytest.approx(30.4)

    updater.data = {"sessionEnergy": "20", "state": 4}
    # 0.20×80 + 20×0.9 = 34 kWh
    assert sensor._get_sensor_value() == 34

    values["input_number.ev_initial_soc"] = 30
    sensor._soc_calculator.invalidate_cache()

    # 0.30×80 + 20×0.9 = 24 + 18 = 42 kWh
    assert sensor._get_sensor_value() == 42



def test_time_to_target_returns_helper_required_for_missing_helpers() -> None:
    sensor = TimeToTargetSocSensor(
        EveusTestUpdater({"sessionEnergy": "16", "powerMeas": "7000"})
    )
    sensor.hass = HelperHass({})

    assert sensor._get_sensor_value() == "Helpers Required"


def test_input_entities_status_sensor_reports_missing_invalid_and_ready() -> None:
    sensor = InputEntitiesStatusSensor(EveusTestUpdater({}))
    sensor.hass = HelperHass({})

    assert sensor._get_sensor_value() == "Optional - 4 Missing"
    sensor._update_extra_state_attributes()
    attrs = sensor.extra_state_attributes
    assert attrs["missing_count"] == 4
    assert "configuration_help" in attrs

    invalid = dict(EV_HELPERS)
    invalid["input_number.ev_target_soc"] = "bad"
    sensor.hass = HelperHass(invalid)
    sensor._last_check_time = 0
    assert sensor._get_sensor_value() == "Invalid 1 Inputs"

    invalid["input_number.ev_target_soc"] = 150
    sensor.hass = HelperHass(invalid)
    sensor._last_check_time = 0
    assert sensor._get_sensor_value() == "Invalid 1 Inputs"

    sensor.hass = HelperHass(EV_HELPERS)
    sensor._last_check_time = 0
    assert sensor._get_sensor_value() == "All Present"


def test_soc_calculator_uses_cache_until_invalidated() -> None:
    calculator = CachedSOCCalculator(cache_ttl=3600)
    hass = HelperHass(EV_HELPERS)

    assert calculator.are_helpers_available(hass) is True
    assert calculator.are_helpers_available(hass) is True
    assert hass.states.calls == [
        "input_number.ev_initial_soc",
        "input_number.ev_battery_capacity",
        "input_number.ev_soc_correction",
        "input_number.ev_target_soc",
    ]


def test_soc_calculator_handles_optional_target_parse_errors() -> None:
    calculator = CachedSOCCalculator()
    helpers = dict(EV_HELPERS)
    helpers["input_number.ev_target_soc"] = "bad"

    assert calculator.are_helpers_available(HelperHass(helpers)) is True
    assert calculator.target_soc is None


def test_soc_calculator_contains_unexpected_state_errors() -> None:
    class BrokenStates:
        def get(self, entity_id: str):
            raise RuntimeError("state machine unavailable")

    calculator = CachedSOCCalculator()

    assert calculator.are_helpers_available(SimpleNamespace(states=BrokenStates())) is False
    assert calculator.get_soc_kwh(HelperHass({}), 1) is None
    assert calculator.get_soc_percent(HelperHass({}), 1) is None


def test_soc_calculator_returns_none_when_capacity_cache_is_zero() -> None:
    calculator = CachedSOCCalculator()
    calculator._input_cache.helpers_available = True
    calculator._input_cache.timestamp = 10**9
    calculator._input_cache.initial_soc = 20
    calculator._input_cache.battery_capacity = 0
    calculator._input_cache.soc_correction = 10

    assert calculator.get_soc_percent(HelperHass({}), 1) is None


def test_soc_calculator_contains_soc_math_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    calculator = CachedSOCCalculator()
    monkeypatch.setattr(
        ev_sensors,
        "calculate_soc_kwh",
        lambda *args: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert calculator.get_soc_kwh(HelperHass(EV_HELPERS), 1) is None


def test_helper_sensor_added_to_hass_contains_tracking_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def noop_added_to_hass(self):
        return None

    def broken_track(*args, **kwargs):
        raise RuntimeError("cannot track")

    sensor = EVSocKwhSensor(EveusTestUpdater({}))
    sensor.hass = HelperHass(EV_HELPERS)
    monkeypatch.setattr(ev_sensors.EveusSensorBase, "async_added_to_hass", noop_added_to_hass)
    monkeypatch.setattr(ev_sensors, "async_track_state_change_event", broken_track)

    asyncio.run(BaseEVHelperSensor.async_added_to_hass(sensor))

    assert sensor._helpers_available is True


def test_helper_sensor_input_change_writes_for_changed_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sensor = EVSocKwhSensor(EveusTestUpdater({"sessionEnergy": "1"}))
    sensor.hass = HelperHass(EV_HELPERS)
    writes = 0

    def write_state() -> None:
        nonlocal writes
        writes += 1

    monkeypatch.setattr(ev_sensors.time, "time", lambda: 100.0)
    sensor.async_write_ha_state = write_state

    sensor._on_input_changed(SimpleNamespace())

    assert writes == 1
    assert sensor._last_update_time == 100.0


def test_helper_sensor_resolve_remaining_inputs_edge_cases() -> None:
    sensor = TimeToTargetSocSensor(EveusTestUpdater({"powerMeas": "bad", "sessionEnergy": "1"}))
    sensor.hass = HelperHass(EV_HELPERS)
    assert sensor._resolve_remaining_inputs() is None

    sensor = TimeToTargetSocSensor(EveusTestUpdater({"powerMeas": "7000", "sessionEnergy": "-1"}))
    sensor.hass = HelperHass(EV_HELPERS)
    assert sensor._resolve_remaining_inputs() is None

    no_target = {k: v for k, v in EV_HELPERS.items() if k != "input_number.ev_target_soc"}
    sensor = TimeToTargetSocSensor(EveusTestUpdater({"powerMeas": "7000", "sessionEnergy": "1"}))
    sensor.hass = HelperHass(no_target)
    assert sensor._resolve_remaining_inputs() is None


def test_time_to_target_contains_calculation_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    sensor = TimeToTargetSocSensor(EveusTestUpdater({"sessionEnergy": "1", "powerMeas": "7000"}))
    sensor.hass = HelperHass(EV_HELPERS)
    sensor._cached_value = "previous"
    monkeypatch.setattr(
        ev_sensors,
        "calculate_remaining_time",
        lambda *args: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert sensor._get_sensor_value() == "previous"


def test_charging_finish_time_rounds_to_next_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed_now = ev_sensors.datetime(2026, 5, 22, 10, 0, 30)
    monkeypatch.setattr(ev_sensors.dt_util, "utcnow", lambda: fixed_now)
    monkeypatch.setattr(ev_sensors, "calculate_remaining_seconds", lambda *args: 90)
    sensor = ChargingFinishTimeSensor(EveusTestUpdater({"sessionEnergy": "1", "powerMeas": "7000"}))
    sensor.hass = HelperHass(EV_HELPERS)

    assert sensor._get_sensor_value() == ev_sensors.datetime(2026, 5, 22, 10, 3)


def test_charging_finish_time_returns_none_for_non_eta_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sensor = ChargingFinishTimeSensor(EveusTestUpdater({"sessionEnergy": "1", "powerMeas": "7000"}))
    sensor.hass = HelperHass(EV_HELPERS)
    monkeypatch.setattr(ev_sensors, "calculate_remaining_seconds", lambda *args: 0)
    assert sensor._get_sensor_value() is None

    monkeypatch.setattr(ev_sensors, "calculate_remaining_seconds", lambda *args: None)
    assert sensor._get_sensor_value() is None

    monkeypatch.setattr(
        ev_sensors,
        "calculate_remaining_seconds",
        lambda *args: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert sensor._get_sensor_value() is None


def test_input_entities_status_sensor_caches_between_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sensor = InputEntitiesStatusSensor(EveusTestUpdater({}))
    sensor.hass = HelperHass(EV_HELPERS)
    monkeypatch.setattr(ev_sensors.time, "time", lambda: 100.0)

    assert sensor._get_sensor_value() == "All Present"
    sensor.hass = HelperHass({})
    assert sensor._get_sensor_value() == "All Present"


def test_input_entities_status_sensor_contains_attribute_and_check_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sensor = InputEntitiesStatusSensor(EveusTestUpdater({}))
    sensor.hass = HelperHass(EV_HELPERS)

    monkeypatch.setattr(
        sensor,
        "_build_extra_state_attributes",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert sensor._update_extra_state_attributes() is True
    assert sensor.extra_state_attributes == {}

    class BrokenStates:
        def get(self, entity_id: str):
            raise RuntimeError("state problem")

    sensor.hass = SimpleNamespace(states=BrokenStates())
    sensor._check_inputs()
    assert sensor._state == "Error"
