"""Unit tests for EV helper sensor edge cases."""
from __future__ import annotations

import asyncio
import logging

import pytest

from conftest import EV_HELPERS, EveusTestUpdater, HelperHass
from custom_components.eveus import ev_sensors
from custom_components.eveus.ev_sensors import (
    BaseEVHelperSensor,
    CachedSOCCalculator,
    ChargingFinishTimeSensor,
    EVSocKwhSensor,
    EVSocPercentSensor,
    TimeToTargetSocSensor,
)

# Map input_number.* helper ids → CachedSOCCalculator.set_value keys.
_HELPER_KEYS = {
    "input_number.ev_initial_soc": "initial_soc",
    "input_number.ev_battery_capacity": "battery_capacity",
    "input_number.ev_soc_correction": "soc_correction",
    "input_number.ev_target_soc": "target_soc",
}


def push_helpers(calc: CachedSOCCalculator, values: dict) -> CachedSOCCalculator:
    """Push a dict of input_number.* values onto a calculator via set_value."""
    for entity_id, key in _HELPER_KEYS.items():
        if entity_id in values:
            calc.set_value(key, values[entity_id])
    return calc


def test_soc_calculator_reports_missing_and_invalid_helpers() -> None:
    calculator = CachedSOCCalculator()

    # No values pushed yet → helpers unavailable.
    assert calculator.are_helpers_available() is False

    # A missing REQUIRED value (cleared via None) keeps SOC disabled.
    push_helpers(calculator, EV_HELPERS)
    calculator.set_value("battery_capacity", None)
    assert calculator.are_helpers_available() is False

    # target_soc is OPTIONAL: absent target must not disable SOC.
    no_target = {k: v for k, v in EV_HELPERS.items() if k != "input_number.ev_target_soc"}
    calculator = push_helpers(CachedSOCCalculator(), no_target)
    assert calculator.are_helpers_available() is True
    assert calculator.target_soc is None
    assert calculator.battery_capacity == 80
    assert calculator.get_soc_percent(0) == 20  # Initial SOC fallback


def test_missing_optional_soc_helpers_are_quiet_at_normal_log_levels(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calculator = CachedSOCCalculator()

    with caplog.at_level(logging.INFO, logger="custom_components.eveus.ev_sensors"):
        assert calculator.are_helpers_available() is False

    assert caplog.records == []


def test_soc_calculator_percent_and_properties() -> None:
    calculator = push_helpers(CachedSOCCalculator(), EV_HELPERS)

    assert calculator.get_soc_percent(16) == 38
    assert calculator.battery_capacity == 80
    assert calculator.soc_correction == 10
    assert calculator.target_soc == 80


def test_soc_sensors_return_values_and_cache_last_valid_value() -> None:
    calculator = push_helpers(CachedSOCCalculator(), EV_HELPERS)
    updater = EveusTestUpdater({"sessionEnergy": "16"})

    kwh = EVSocKwhSensor(updater, 1, calculator)
    percent = EVSocPercentSensor(updater, 1, calculator)

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
    calculator = push_helpers(CachedSOCCalculator(), EV_HELPERS)
    sensor = TimeToTargetSocSensor(
        EveusTestUpdater({"sessionEnergy": "16", "powerMeas": "0"}), 1, calculator
    )

    assert sensor._get_sensor_value() == "Not charging"


def test_helper_sensors_subscribe_to_soc_dispatcher_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[str] = []
    cleanup_callbacks: list[object] = []

    async def noop_added_to_hass(self):
        return None

    def fake_dispatcher_connect(hass, signal, target):
        signals.append(signal)
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
        "async_dispatcher_connect",
        fake_dispatcher_connect,
    )

    asyncio.run(BaseEVHelperSensor.async_added_to_hass(sensor))

    assert signals == [ev_sensors.soc_update_signal("entry-id")]
    assert len(cleanup_callbacks) == 1


def test_helper_sensor_available_property_is_pure() -> None:
    # SOC%/kWh are available whenever online; availability never reads hass.states.
    sensor = EVSocKwhSensor(EveusTestUpdater({"sessionEnergy": "16"}))
    hass = HelperHass(EV_HELPERS)
    sensor.hass = hass

    assert sensor.available is True
    assert hass.states.calls == []


def test_helper_sensor_coordinator_update_computes_value_when_online() -> None:
    writes = 0
    calculator = push_helpers(CachedSOCCalculator(), EV_HELPERS)
    sensor = EVSocKwhSensor(EveusTestUpdater({"sessionEnergy": "16"}), 1, calculator)
    sensor.hass = HelperHass(EV_HELPERS)

    def record_write() -> None:
        nonlocal writes
        writes += 1

    sensor.async_write_ha_state = record_write

    # SOC%/kWh are available whenever online (no helper gate).
    assert sensor.available is True
    sensor._handle_coordinator_update()

    assert sensor.available is True
    # sessionEnergy=16, initial=20%, capacity=80, loss=10 → 16 + 14.4 = 30.4 kWh
    assert sensor.native_value == pytest.approx(30.4)
    assert writes == 1


def test_soc_reprojects_when_initial_soc_changes() -> None:
    """4.6.0: SoC = initial_soc% × capacity + sessionEnergy × efficiency.
    Mid-session correction of initial_soc reprojects on the next poll without
    any baseline machinery — there is nothing to invalidate."""
    calculator = push_helpers(CachedSOCCalculator(), EV_HELPERS)
    updater = EveusTestUpdater({"sessionEnergy": "16", "state": 4})
    sensor = EVSocKwhSensor(updater, 1, calculator)

    # 0.20×80 + 16×0.9 = 30.4 kWh
    assert sensor._get_sensor_value() == pytest.approx(30.4)

    updater.data = {"sessionEnergy": "20", "state": 4}
    # 0.20×80 + 20×0.9 = 34 kWh
    assert sensor._get_sensor_value() == 34

    # A fresh Initial SOC value is pushed by the number entity.
    calculator.set_value("initial_soc", 30)

    # 0.30×80 + 20×0.9 = 24 + 18 = 42 kWh
    assert sensor._get_sensor_value() == 42



def test_time_to_target_returns_helper_required_for_missing_helpers() -> None:
    sensor = TimeToTargetSocSensor(
        EveusTestUpdater({"sessionEnergy": "16", "powerMeas": "7000"})
    )
    sensor.hass = HelperHass({})

    assert sensor._get_sensor_value() == "Helpers Required"


def test_soc_calculator_optional_target_absent_keeps_core_available() -> None:
    # target_soc is optional: core SOC stays available without it.
    no_target = {k: v for k, v in EV_HELPERS.items() if k != "input_number.ev_target_soc"}
    calculator = push_helpers(CachedSOCCalculator(), no_target)

    assert calculator.are_helpers_available() is True
    assert calculator.target_soc is None


def test_soc_calculator_returns_none_when_no_values_pushed() -> None:
    calculator = CachedSOCCalculator()

    assert calculator.are_helpers_available() is False
    assert calculator.get_soc_kwh(1) is None
    assert calculator.get_soc_percent(1) is None


def test_soc_calculator_returns_none_when_capacity_is_zero() -> None:
    calculator = CachedSOCCalculator()
    calculator.set_value("initial_soc", 20)
    calculator.set_value("battery_capacity", 0)
    calculator.set_value("soc_correction", 10)

    assert calculator.get_soc_percent(1) is None


def test_soc_calculator_contains_soc_math_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    calculator = push_helpers(CachedSOCCalculator(), EV_HELPERS)
    monkeypatch.setattr(
        ev_sensors,
        "calculate_soc_kwh",
        lambda *args: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert calculator.get_soc_kwh(1) is None


def test_helper_sensor_soc_input_change_writes_for_changed_value() -> None:
    calculator = push_helpers(CachedSOCCalculator(), EV_HELPERS)
    sensor = EVSocKwhSensor(EveusTestUpdater({"sessionEnergy": "1"}), 1, calculator)
    sensor.hass = HelperHass(EV_HELPERS)
    writes = 0

    def write_state() -> None:
        nonlocal writes
        writes += 1

    sensor.async_write_ha_state = write_state

    # A pushed SOC value changes the computed native value → one write.
    sensor._on_soc_input_changed()

    assert writes == 1


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
    calculator = push_helpers(CachedSOCCalculator(), EV_HELPERS)
    sensor = TimeToTargetSocSensor(
        EveusTestUpdater({"sessionEnergy": "1", "powerMeas": "7000"}), 1, calculator
    )
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
    calculator = push_helpers(CachedSOCCalculator(), EV_HELPERS)
    sensor = ChargingFinishTimeSensor(
        EveusTestUpdater({"sessionEnergy": "1", "powerMeas": "7000"}), 1, calculator
    )
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


def test_soc_percent_available_without_target() -> None:
    """SOC% is available once core inputs are set, even without target_soc."""
    calculator = CachedSOCCalculator()
    calculator.set_value("initial_soc", 20)
    calculator.set_value("battery_capacity", 50)
    calculator.set_value("soc_correction", 7.5)

    sensor = EVSocPercentSensor(
        EveusTestUpdater({"sessionEnergy": 5.0}), 1, calculator
    )
    sensor.hass = HelperHass({})

    assert calculator.target_soc is None
    assert sensor.available is True
    assert sensor._get_sensor_value() is not None
