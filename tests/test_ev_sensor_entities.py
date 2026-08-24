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


def test_soc_calculator_ignores_unknown_pushed_keys() -> None:
    calculator = CachedSOCCalculator()

    calculator.set_value("future_key", 123)

    assert calculator.are_helpers_available() is False
    assert not hasattr(calculator, "future_key")


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


def test_soc_sensors_return_unknown_for_invalid_session_energy() -> None:
    calculator = push_helpers(CachedSOCCalculator(), EV_HELPERS)
    updater = EveusTestUpdater({"sessionEnergy": "16"})
    kwh = EVSocKwhSensor(updater, 1, calculator)
    percent = EVSocPercentSensor(updater, 1, calculator)

    assert kwh._get_sensor_value() == pytest.approx(30.4)
    assert percent._get_sensor_value() == 38

    updater.data = {"sessionEnergy": "-1"}

    assert kwh._get_sensor_value() is None
    assert percent._get_sensor_value() is None


def test_soc_sensors_go_unknown_when_helper_inputs_disappear() -> None:
    """Regression test for B04: get_soc_kwh/get_soc_percent return None once a
    SOC helper input is gone (not a transient poll blip) — the sensor must go
    unknown instead of freezing on the last computed value.
    """
    calculator = push_helpers(CachedSOCCalculator(), EV_HELPERS)
    updater = EveusTestUpdater({"sessionEnergy": "16"})
    kwh = EVSocKwhSensor(updater, 1, calculator)
    percent = EVSocPercentSensor(updater, 1, calculator)

    assert kwh._get_sensor_value() == pytest.approx(30.4)
    assert percent._get_sensor_value() == 38
    calculator.set_value("battery_capacity", None)

    assert kwh._get_sensor_value() is None
    assert percent._get_sensor_value() is None


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


def test_required_helper_sensor_is_unavailable_until_inputs_are_pushed() -> None:
    class RequiredSensor(BaseEVHelperSensor):
        ENTITY_NAME = "Required Helper"
        _requires_helpers = True

    sensor = RequiredSensor(EveusTestUpdater({}))

    assert sensor.available is False

    push_helpers(sensor._soc_calculator, EV_HELPERS)

    assert sensor.available is True


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


def test_helper_sensor_soc_input_change_is_quiet_when_nothing_changes() -> None:
    calculator = push_helpers(CachedSOCCalculator(), EV_HELPERS)
    sensor = EVSocKwhSensor(EveusTestUpdater({"sessionEnergy": "16"}), 1, calculator)
    sensor.hass = HelperHass(EV_HELPERS)
    writes = 0

    def write_state() -> None:
        nonlocal writes
        writes += 1

    sensor.async_write_ha_state = write_state
    sensor._update_native_value()

    sensor._on_soc_input_changed()

    assert writes == 0


def test_helper_sensor_coordinator_update_is_quiet_when_nothing_changes() -> None:
    calculator = push_helpers(CachedSOCCalculator(), EV_HELPERS)
    sensor = EVSocKwhSensor(EveusTestUpdater({"sessionEnergy": "16"}), 1, calculator)
    sensor.hass = HelperHass(EV_HELPERS)
    writes = 0

    def write_state() -> None:
        nonlocal writes
        writes += 1

    sensor.async_write_ha_state = write_state
    sensor._handle_coordinator_update()
    writes = 0

    sensor._handle_coordinator_update()

    assert writes == 0


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



def test_time_to_target_unknown_when_inputs_not_pushed() -> None:
    # No SOC inputs pushed yet (e.g. the startup window before the native
    # number entities load): the ETA can't be computed, so it reports unknown
    # (None) rather than a placeholder string.
    sensor = TimeToTargetSocSensor(
        EveusTestUpdater({"sessionEnergy": "16", "powerMeas": "7000"})
    )
    sensor.hass = HelperHass({})

    assert sensor._get_sensor_value() is None


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


def test_charging_finish_time_contains_calculation_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calculator = push_helpers(CachedSOCCalculator(), EV_HELPERS)
    sensor = ChargingFinishTimeSensor(
        EveusTestUpdater({"sessionEnergy": "16", "powerMeas": "7000"}),
        1,
        calculator,
    )
    monkeypatch.setattr(
        ev_sensors,
        "calculate_remaining_seconds",
        lambda *args: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert sensor._get_sensor_value() is None


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


def test_helper_sensor_soc_input_change_skips_stale_failed_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_now = ev_sensors.datetime(2026, 6, 18, 10, 0, 0)
    monkeypatch.setattr(ev_sensors.dt_util, "utcnow", lambda: fixed_now)
    calculator = push_helpers(CachedSOCCalculator(), EV_HELPERS)
    updater = EveusTestUpdater(
        {"state": 4, "sessionEnergy": "1", "powerMeas": "7000"},
        available=False,
    )
    updater.last_update_success = False
    sensor = ChargingFinishTimeSensor(updater, 1, calculator)
    sensor.async_write_ha_state = lambda: None
    sensor._attr_native_value = ev_sensors.datetime(2026, 6, 18, 10, 30)

    sensor._on_soc_input_changed()

    assert sensor.native_value == ev_sensors.datetime(2026, 6, 18, 10, 30)


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


def test_resolve_remaining_inputs_none_when_only_power_meas_invalid() -> None:
    """power_meas is None (bad telemetry) but energy_charged is valid: must
    still go unknown (the `or` in the None-check, not `and`) rather than
    falling through to compare None against MAX_POWER_W."""
    calculator = push_helpers(CachedSOCCalculator(), EV_HELPERS)
    sensor = TimeToTargetSocSensor(
        EveusTestUpdater({"powerMeas": "bad", "sessionEnergy": "1", "state": 4}),
        1,
        calculator,
    )
    sensor.hass = HelperHass(EV_HELPERS)

    assert sensor._resolve_remaining_inputs() is None


def test_resolve_remaining_inputs_accepts_power_meas_at_max_ceiling() -> None:
    """MAX_POWER_W itself is a valid (inclusive) reading, not an outlier."""
    from custom_components.eveus.const import MAX_POWER_W

    calculator = push_helpers(CachedSOCCalculator(), EV_HELPERS)
    sensor = TimeToTargetSocSensor(
        EveusTestUpdater(
            {"powerMeas": str(MAX_POWER_W), "sessionEnergy": "1", "state": 4}
        ),
        1,
        calculator,
    )
    sensor.hass = HelperHass(EV_HELPERS)

    assert sensor._resolve_remaining_inputs() is not None


def test_energy_charged_accepts_value_at_max_ceiling() -> None:
    """MAX_ENERGY_KWH itself is a valid (inclusive) reading, not an outlier."""
    from custom_components.eveus.const import MAX_ENERGY_KWH

    sensor = EVSocKwhSensor(EveusTestUpdater({"sessionEnergy": str(MAX_ENERGY_KWH)}))

    assert sensor._get_energy_charged() == MAX_ENERGY_KWH


def test_time_to_target_caches_the_computed_result_on_success() -> None:
    """A successful computation must cache the actual result, not None —
    otherwise a later stale-refresh read would show unknown instead of the
    last good value."""
    calculator = push_helpers(CachedSOCCalculator(), EV_HELPERS)
    sensor = TimeToTargetSocSensor(
        EveusTestUpdater({"sessionEnergy": "0", "powerMeas": "7000", "state": 4}),
        1,
        calculator,
    )
    sensor.hass = HelperHass(EV_HELPERS)

    result = sensor._get_sensor_value()

    assert result is not None
    assert sensor._cached_value == result


def test_time_to_target_drops_stale_value_on_calculation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for B03: the docstring promises unknown (None) on
    failure, "instead of freezing it" — the except branch must not return
    the stale cached value.
    """
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

    assert sensor._get_sensor_value() is None
    assert sensor._cached_value is None


def test_charging_finish_time_rounds_up_to_the_ten_minute_grid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_now = ev_sensors.datetime(2026, 5, 22, 10, 0, 30)
    monkeypatch.setattr(ev_sensors.dt_util, "utcnow", lambda: fixed_now)
    monkeypatch.setattr(ev_sensors, "calculate_remaining_seconds", lambda *args: 90)
    calculator = push_helpers(CachedSOCCalculator(), EV_HELPERS)
    sensor = ChargingFinishTimeSensor(
        EveusTestUpdater({"sessionEnergy": "1", "powerMeas": "7000"}), 1, calculator
    )
    sensor.hass = HelperHass(EV_HELPERS)

    assert sensor._get_sensor_value() == ev_sensors.datetime(2026, 5, 22, 10, 10)


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

    # Inputs unavailable → must reset to unknown, not keep showing "2h 15m".
    assert sensor._get_sensor_value() is None
    assert sensor._cached_value is None


from custom_components.eveus.utils import calculate_soc_kwh, calculate_soc_percent
from conftest import EV_HELPERS, HelperHass, disable_state_writes


def test_time_to_target_unknown_when_target_missing() -> None:
    calc = CachedSOCCalculator()
    calc.set_value("initial_soc", EV_HELPERS["input_number.ev_initial_soc"])
    calc.set_value("battery_capacity", EV_HELPERS["input_number.ev_battery_capacity"])
    calc.set_value("soc_correction", EV_HELPERS["input_number.ev_soc_correction"])
    updater = EveusTestUpdater(data={"powerMeas": 5000, "sessionEnergy": 10})
    sensor = TimeToTargetSocSensor(updater, 1, calc)
    disable_state_writes(sensor)

    assert sensor._get_sensor_value() is None


def test_time_to_target_unknown_when_no_inputs() -> None:
    updater = EveusTestUpdater(data={"powerMeas": 5000, "sessionEnergy": 10})
    sensor = TimeToTargetSocSensor(updater, 1, CachedSOCCalculator())
    sensor.hass = HelperHass({})
    disable_state_writes(sensor)

    assert sensor._get_sensor_value() is None


import pytest


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


def _soc_calc() -> CachedSOCCalculator:
    calc = CachedSOCCalculator()
    calc.set_value("initial_soc", 20)
    calc.set_value("battery_capacity", 50)
    calc.set_value("soc_correction", 10)
    calc.set_value("target_soc", 80)
    return calc


def test_soc_percent_rejects_session_energy_outlier() -> None:
    bad = EVSocPercentSensor(EveusTestUpdater(data={"sessionEnergy": 1e100}), 1, _soc_calc())
    assert bad._get_sensor_value() is None
    good = EVSocPercentSensor(EveusTestUpdater(data={"sessionEnergy": 10}), 1, _soc_calc())
    assert good._get_sensor_value() is not None


def test_eta_rejects_power_outlier() -> None:
    bad = TimeToTargetSocSensor(
        EveusTestUpdater(data={"sessionEnergy": 10, "powerMeas": 1e100, "state": 4}), 1, _soc_calc()
    )
    assert bad._get_sensor_value() is None
    good = TimeToTargetSocSensor(
        EveusTestUpdater(data={"sessionEnergy": 10, "powerMeas": 3000, "state": 4}), 1, _soc_calc()
    )
    assert isinstance(good._get_sensor_value(), str)


def test_time_to_target_unknown_when_telemetry_missing() -> None:
    calc = CachedSOCCalculator()
    calc.set_value("initial_soc", 20)
    calc.set_value("battery_capacity", 50)
    calc.set_value("soc_correction", 7.5)
    calc.set_value("target_soc", 80)  # all SOC inputs present

    # Updater online but payload carries no power/SOC telemetry.
    updater = EveusTestUpdater({"state": 4})
    sensor = TimeToTargetSocSensor(updater, 1, calc)

    assert sensor._get_sensor_value() is None


def test_eta_not_charging_when_state_inactive() -> None:
    calc = CachedSOCCalculator()
    for key, value in (
        ("initial_soc", 20.0),
        ("battery_capacity", 80.0),
        ("soc_correction", 10.0),
        ("target_soc", 80.0),
    ):
        calc.set_value(key, value)

    # Residual standby power in a non-charging state must not fabricate an ETA.
    sensor = TimeToTargetSocSensor(
        EveusTestUpdater({"sessionEnergy": "10", "powerMeas": "50", "state": 2}),
        1,
        calc,
    )
    assert sensor._get_sensor_value() == "Not charging"




def _push_helpers_ev(calc):
    for entity_id, value in EV_HELPERS.items():
        calc.set_value(entity_id.removeprefix("input_number.ev_"), float(value))
    return calc


def test_energy_to_target_does_not_zero_from_percent_rounding() -> None:
    from custom_components.eveus.ev_sensors import (
        CachedSOCCalculator as _CSC,
        EnergyToTargetSocSensor,
    )
    calc = _push_helpers_ev(_CSC())
    calc.set_value("target_soc", 84.0)
    sensor = EnergyToTargetSocSensor(
        EveusTestUpdater({"sessionEnergy": "56.4"}), 1, calc
    )
    value = sensor._get_sensor_value()
    assert value is not None
    # 0.44 kWh battery / 0.9 efficiency ≈ 0.49 kWh from the grid — must not be 0.
    assert value == pytest.approx(0.49, abs=0.02)


def test_cost_to_target_zero_at_target_without_tariff() -> None:
    from custom_components.eveus.ev_sensors import (
        CachedSOCCalculator as _CSC,
        CostToTargetSocSensor,
    )
    calc = _push_helpers_ev(_CSC())
    calc.set_value("target_soc", 20.0)  # already at target
    sensor = CostToTargetSocSensor(EveusTestUpdater({"sessionEnergy": "0"}), 1, calc)
    # No tariff fields in the payload at all — cost is still exactly zero.
    assert sensor._get_sensor_value() == 0.0


def test_cost_to_target_monetary_metadata() -> None:
    from homeassistant.components.sensor import SensorDeviceClass
    from custom_components.eveus.ev_sensors import (
        CachedSOCCalculator as _CSC,
        CostToTargetSocSensor,
    )

    calc = _push_helpers_ev(_CSC())
    sensor = CostToTargetSocSensor(EveusTestUpdater({}), 1, calc)
    assert sensor._attr_device_class == SensorDeviceClass.MONETARY
    assert sensor._attr_state_class is None
    assert sensor._attr_native_unit_of_measurement == "UAH"


def test_v11_soc_limit_does_not_stop_before_exact_target():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from custom_components.eveus.soc_limit import SocLimitController

    calc = CachedSOCCalculator()
    calc.set_value("initial_soc", 20)
    calc.set_value("battery_capacity", 50)
    calc.set_value("soc_correction", 0)
    calc.set_value("target_soc", 80)

    u = MagicMock()
    u.available = True
    u.last_update_success = True
    u.device_number = 1
    u.data = {"state": 4, "sessionEnergy": 29.8, "evseEnabled": 0, "suspendLimits": 0}
    u.send_command = AsyncMock(return_value=True)

    hass = MagicMock()
    hass.async_create_task = lambda coro: asyncio.run(coro)
    hass.bus.async_fire = MagicMock()
    ctrl = SocLimitController(hass, u, calc)
    ctrl.set_enabled(True)
    ctrl.process()
    u.send_command.assert_not_called()


def test_v11_calculator_exposes_exact_percent():
    calc = CachedSOCCalculator()
    calc.set_value("initial_soc", 20)
    calc.set_value("battery_capacity", 50)
    calc.set_value("soc_correction", 0)
    calc.set_value("target_soc", 80)
    # 10 kWh initial + 29.8 kWh = 39.8 kWh on 50 kWh = 79.6% exact
    exact = calc.get_soc_percent_exact(29.8)
    assert 79.5 < exact < 79.7
    # the displayed percent still rounds
    assert calc.get_soc_percent(29.8) == 80


def test_zero_soc_correction_is_preserved() -> None:
    from custom_components.eveus import ev_sensors

    calc = ev_sensors.CachedSOCCalculator()
    calc.set_value("soc_correction", 0.0)
    assert calc._effective_correction() == 0.0
    assert calc.soc_correction == 0.0


def test_missing_soc_correction_falls_back_to_default() -> None:
    from custom_components.eveus import ev_sensors

    calc = ev_sensors.CachedSOCCalculator()
    calc.set_value("soc_correction", None)
    assert calc._effective_correction() == ev_sensors.DEFAULT_SOC_CORRECTION


def test_session_energy_invalid_when_present_and_negative() -> None:
    from custom_components.eveus import ev_sensors

    sensor = ev_sensors.EVSocKwhSensor(EveusTestUpdater({"sessionEnergy": -1.0}))
    assert sensor._session_energy_is_invalid() is True


def test_session_energy_not_invalid_when_absent() -> None:
    from custom_components.eveus import ev_sensors

    sensor = ev_sensors.EVSocKwhSensor(EveusTestUpdater({}))
    assert sensor._session_energy_is_invalid() is False


def test_soc_inputs_reject_out_of_range_soc() -> None:
    from custom_components.eveus.utils import _validate_soc_inputs

    assert _validate_soc_inputs(-1, 60, 5, 8) is None
    assert _validate_soc_inputs(101, 60, 5, 8) is None


def test_soc_inputs_reject_nonpositive_capacity() -> None:
    from custom_components.eveus.utils import _validate_soc_inputs

    assert _validate_soc_inputs(50, 0, 5, 8) is None
    assert _validate_soc_inputs(50, -10, 5, 8) is None


def test_soc_inputs_reject_negative_energy() -> None:
    from custom_components.eveus.utils import _validate_soc_inputs

    assert _validate_soc_inputs(50, 60, -0.1, 8) is None


def test_soc_inputs_reject_out_of_range_efficiency() -> None:
    from custom_components.eveus.utils import _validate_soc_inputs

    assert _validate_soc_inputs(50, 60, 5, -1) is None
    assert _validate_soc_inputs(50, 60, 5, 100) is None


def test_soc_inputs_accept_valid() -> None:
    from custom_components.eveus.utils import _validate_soc_inputs

    assert _validate_soc_inputs(50, 60, 5, 8) == (50.0, 60.0, 5.0, 8.0)


@pytest.fixture(autouse=False)
def _ha_clock_plus3_ev():
    from datetime import timedelta, timezone as _tz
    from homeassistant.util import dt as dt_util

    original = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(_tz(timedelta(hours=3)))
    yield
    dt_util.set_default_time_zone(original)


def _push_ev_helpers(calc):
    for entity_id, value in EV_HELPERS.items():
        calc.set_value(entity_id.removeprefix("input_number.ev_"), float(value))
    return calc


def test_energy_to_target_unknown_when_session_energy_absent_mid_session(_ha_clock_plus3_ev) -> None:
    from custom_components.eveus.ev_sensors import (
        CachedSOCCalculator,
        EnergyToTargetSocSensor,
    )

    calc = _push_ev_helpers(CachedSOCCalculator())
    sensor = EnergyToTargetSocSensor(
        EveusTestUpdater({"state": 4}), 1, calc
    )
    assert sensor._get_sensor_value() is None


def test_energy_to_target_zero_fallback_outside_active_session(_ha_clock_plus3_ev) -> None:
    from custom_components.eveus.ev_sensors import (
        CachedSOCCalculator,
        EnergyToTargetSocSensor,
    )

    calc = _push_ev_helpers(CachedSOCCalculator())
    sensor = EnergyToTargetSocSensor(
        EveusTestUpdater({"state": 2}), 1, calc
    )
    assert sensor._get_sensor_value() is not None


def test_energy_to_target_has_no_storage_device_class(_ha_clock_plus3_ev) -> None:
    from custom_components.eveus.ev_sensors import (
        CachedSOCCalculator,
        EnergyToTargetSocSensor,
    )

    calc = _push_ev_helpers(CachedSOCCalculator())
    sensor = EnergyToTargetSocSensor(EveusTestUpdater({}), 1, calc)
    assert sensor.device_class is None
    assert sensor._attr_native_unit_of_measurement == "kWh"


def test_reports_grid_energy_needed_to_reach_target(_ha_clock_plus3_ev):
    import pytest
    from custom_components.eveus.ev_sensors import (
        CachedSOCCalculator,
        EnergyToTargetSocSensor,
    )

    calc = _push_ev_helpers(CachedSOCCalculator())
    sensor = EnergyToTargetSocSensor(
        EveusTestUpdater({"sessionEnergy": "16"}), 1, calc
    )
    assert sensor._get_sensor_value() == pytest.approx(37.33, abs=0.01)


def test_reports_zero_when_target_reached(_ha_clock_plus3_ev):
    from custom_components.eveus.ev_sensors import (
        CachedSOCCalculator,
        EnergyToTargetSocSensor,
    )

    calc = _push_ev_helpers(CachedSOCCalculator())
    calc.set_value("target_soc", 20.0)
    sensor = EnergyToTargetSocSensor(EveusTestUpdater({"sessionEnergy": "0"}), 1, calc)
    assert sensor._get_sensor_value() == 0.0


def test_unknown_without_target_soc(_ha_clock_plus3_ev):
    from custom_components.eveus.ev_sensors import (
        CachedSOCCalculator,
        EnergyToTargetSocSensor,
    )

    calc = _push_ev_helpers(CachedSOCCalculator())
    calc.set_value("target_soc", None)
    sensor = EnergyToTargetSocSensor(EveusTestUpdater({"sessionEnergy": "16"}), 1, calc)
    assert sensor._get_sensor_value() is None


def test_unknown_when_session_energy_corrupt(_ha_clock_plus3_ev):
    from custom_components.eveus.ev_sensors import (
        CachedSOCCalculator,
        EnergyToTargetSocSensor,
    )

    calc = _push_ev_helpers(CachedSOCCalculator())
    sensor = EnergyToTargetSocSensor(
        EveusTestUpdater({"sessionEnergy": "-5"}), 1, calc
    )
    assert sensor._get_sensor_value() is None


def test_energy_to_target_zero_fallback_is_exactly_zero_not_one(_ha_clock_plus3_ev):
    """Outside an active session with sessionEnergy absent, the fallback must
    be exactly 0.0 kWh delivered (reprojecting purely from Initial SOC), not
    1.0 and not None."""
    import pytest
    from custom_components.eveus.ev_sensors import (
        CachedSOCCalculator,
        EnergyToTargetSocSensor,
    )

    calc = _push_ev_helpers(CachedSOCCalculator())
    sensor = EnergyToTargetSocSensor(EveusTestUpdater({"state": 2}), 1, calc)

    # initial=20%*80=16kWh; target=80%*80=64kWh; remaining=48kWh; /0.9 loss = 53.33
    assert sensor._get_sensor_value() == pytest.approx(53.33, abs=0.01)


def test_energy_to_target_none_when_kwh_calc_fails_even_with_capacity_set(
    _ha_clock_plus3_ev, monkeypatch: pytest.MonkeyPatch
):
    """current_kwh is None (calc failure) while battery_capacity is still
    truthy: must go unknown (the `or`, not `and`) instead of crashing on
    `target_soc * battery_capacity / 100 - None`."""
    from custom_components.eveus.ev_sensors import (
        CachedSOCCalculator,
        EnergyToTargetSocSensor,
    )

    calc = _push_ev_helpers(CachedSOCCalculator())
    sensor = EnergyToTargetSocSensor(EveusTestUpdater({"sessionEnergy": "16"}), 1, calc)
    monkeypatch.setattr(calc, "get_soc_kwh", lambda energy_charged: None)

    assert sensor._get_sensor_value() is None


def test_energy_to_target_zero_at_target_even_with_invalid_correction(
    _ha_clock_plus3_ev, monkeypatch: pytest.MonkeyPatch
):
    """At exactly the target (remaining == 0), the result must be 0.0
    unconditionally — the early return must not fall through to the
    correction-range check (which would reject an out-of-range correction
    and return None instead of the already-known-correct 0.0).

    get_soc_kwh is mocked directly so the current-kWh calculation and the
    end-of-function correction read are decoupled (both would otherwise draw
    from the same soc_correction field)."""
    from custom_components.eveus.ev_sensors import (
        CachedSOCCalculator,
        EnergyToTargetSocSensor,
    )

    calc = CachedSOCCalculator()
    calc.set_value("initial_soc", 20)
    calc.set_value("battery_capacity", 50)
    calc.set_value("soc_correction", 10)
    calc.set_value("target_soc", 80)  # 80% of 50kWh = 40kWh
    sensor = EnergyToTargetSocSensor(EveusTestUpdater({"sessionEnergy": "0"}), 1, calc)
    monkeypatch.setattr(calc, "get_soc_kwh", lambda energy_charged: 40.0)  # exactly at target
    monkeypatch.setattr(type(calc), "soc_correction", 100)  # invalid if the check ran

    assert sensor._get_sensor_value() == 0.0


def test_energy_to_target_accepts_zero_correction(_ha_clock_plus3_ev):
    """soc_correction=0 (explicit no-loss config) is a valid lower boundary,
    not an out-of-range value."""
    from custom_components.eveus.ev_sensors import (
        CachedSOCCalculator,
        EnergyToTargetSocSensor,
    )

    calc = CachedSOCCalculator()
    calc.set_value("initial_soc", 20)
    calc.set_value("battery_capacity", 80)
    calc.set_value("soc_correction", 0)
    calc.set_value("target_soc", 80)
    sensor = EnergyToTargetSocSensor(EveusTestUpdater({"sessionEnergy": "0"}), 1, calc)

    assert sensor._get_sensor_value() is not None


def test_energy_to_target_rejects_correction_at_100(_ha_clock_plus3_ev):
    """soc_correction=100 would divide by zero; it must be rejected (None),
    not accepted through to the division."""
    from custom_components.eveus.ev_sensors import (
        CachedSOCCalculator,
        EnergyToTargetSocSensor,
    )

    calc = CachedSOCCalculator()
    calc.set_value("initial_soc", 20)
    calc.set_value("battery_capacity", 80)
    calc.set_value("soc_correction", 100)
    calc.set_value("target_soc", 80)
    sensor = EnergyToTargetSocSensor(EveusTestUpdater({"sessionEnergy": "0"}), 1, calc)

    assert sensor._get_sensor_value() is None


def test_energy_to_target_rounds_to_two_decimals_not_three(_ha_clock_plus3_ev):
    """The public value is rounded to 2 decimals, matching the sensor's
    suggested_display_precision of 1 — not left at 3-decimal precision."""
    from custom_components.eveus.ev_sensors import (
        CachedSOCCalculator,
        EnergyToTargetSocSensor,
    )

    calc = _push_ev_helpers(CachedSOCCalculator())
    sensor = EnergyToTargetSocSensor(
        EveusTestUpdater({"sessionEnergy": "16"}), 1, calc
    )

    # 33.6kWh remaining battery / 0.9 efficiency = 37.333333... -> 37.33, not 37.333.
    assert sensor._get_sensor_value() == 37.33


def test_cost_to_target_rounds_to_two_decimals_not_three(
    _ha_clock_plus3_ev, monkeypatch: pytest.MonkeyPatch
):
    """The forecast cost is rounded to 2 decimals, matching the sensor's
    suggested_display_precision of 0 — not left at 3-decimal precision."""
    import custom_components.eveus.sensor_definitions as sensor_definitions
    from custom_components.eveus.ev_sensors import (
        CachedSOCCalculator,
        CostToTargetSocSensor,
    )

    calc = _push_ev_helpers(CachedSOCCalculator())
    sensor = CostToTargetSocSensor(EveusTestUpdater({"sessionEnergy": "16"}), 1, calc)
    monkeypatch.setattr(sensor_definitions, "get_active_rate_cost", lambda *a, **kw: 7)

    # remaining = 37.333333... kWh; * rate 7 = 261.333333... -> 261.33, not 261.333.
    assert sensor._get_sensor_value() == 261.33


def test_charging_finish_time_not_reached_when_one_second_remains(
    _ha_clock_plus3_ev, monkeypatch: pytest.MonkeyPatch
):
    """A 1-second-remaining ETA is still charging (> 0), not "target
    reached" — only seconds<=0 (None/0) means reached/invalid."""
    from custom_components.eveus.ev_sensors import (
        CachedSOCCalculator,
        ChargingFinishTimeSensor,
    )

    calc = _push_ev_helpers(CachedSOCCalculator())
    sensor = ChargingFinishTimeSensor(
        EveusTestUpdater({"sessionEnergy": "16", "powerMeas": "7000", "state": 4}),
        1,
        calc,
    )
    monkeypatch.setattr(ev_sensors, "calculate_remaining_seconds", lambda *args: 1)

    assert sensor._get_sensor_value() is not None


def test_prices_remaining_energy_with_active_tariff(_ha_clock_plus3_ev):
    import pytest
    from custom_components.eveus.ev_sensors import (
        CachedSOCCalculator,
        CostToTargetSocSensor,
    )

    calc = _push_ev_helpers(CachedSOCCalculator())
    sensor = CostToTargetSocSensor(
        EveusTestUpdater({"sessionEnergy": "16", "activeTarif": 0, "tarif": 432}),
        1,
        calc,
    )
    assert sensor._get_sensor_value() == pytest.approx(161.28, abs=0.05)


def test_zero_cost_when_target_reached(_ha_clock_plus3_ev):
    from custom_components.eveus.ev_sensors import (
        CachedSOCCalculator,
        CostToTargetSocSensor,
    )

    calc = _push_ev_helpers(CachedSOCCalculator())
    calc.set_value("target_soc", 20.0)
    sensor = CostToTargetSocSensor(
        EveusTestUpdater({"sessionEnergy": "0", "activeTarif": 0, "tarif": 432}),
        1,
        calc,
    )
    assert sensor._get_sensor_value() == 0.0


def test_unknown_without_tariff(_ha_clock_plus3_ev):
    from custom_components.eveus.ev_sensors import (
        CachedSOCCalculator,
        CostToTargetSocSensor,
    )

    calc = _push_ev_helpers(CachedSOCCalculator())
    sensor = CostToTargetSocSensor(
        EveusTestUpdater({"sessionEnergy": "16"}), 1, calc
    )
    assert sensor._get_sensor_value() is None


def test_uses_rate2_when_active(_ha_clock_plus3_ev):
    import pytest
    from custom_components.eveus.ev_sensors import (
        CachedSOCCalculator,
        CostToTargetSocSensor,
    )

    calc = _push_ev_helpers(CachedSOCCalculator())
    sensor = CostToTargetSocSensor(
        EveusTestUpdater(
            {"sessionEnergy": "16", "activeTarif": 1, "tarifAValue": 216}
        ),
        1,
        calc,
    )
    assert sensor._get_sensor_value() == pytest.approx(80.64, abs=0.05)


# =============================================================================
# Static entity metadata (ENTITY_NAME/_attr_* class constants)
# =============================================================================


def _attr(cls: type, name: str):
    """Read an HA CachedProperties-backed _attr_* class default.

    HA's metaclass turns ``_attr_foo`` into a property on the class; the
    literal default value it was assigned is stashed under the
    name-mangled ``__attr_foo`` key in the class ``__dict__`` instead.
    """
    return vars(cls).get(f"__attr_{name}")


def test_soc_kwh_sensor_metadata() -> None:
    from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
    from homeassistant.const import UnitOfEnergy

    assert EVSocKwhSensor.ENTITY_NAME == "SOC Energy"
    assert _attr(EVSocKwhSensor, "device_class") == SensorDeviceClass.ENERGY_STORAGE
    assert _attr(EVSocKwhSensor, "native_unit_of_measurement") == UnitOfEnergy.KILO_WATT_HOUR
    assert _attr(EVSocKwhSensor, "icon") == "mdi:battery-charging"
    assert _attr(EVSocKwhSensor, "suggested_display_precision") == 1
    assert _attr(EVSocKwhSensor, "state_class") == SensorStateClass.MEASUREMENT


def test_soc_percent_sensor_metadata() -> None:
    from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

    assert EVSocPercentSensor.ENTITY_NAME == "SOC Percent"
    assert _attr(EVSocPercentSensor, "device_class") == SensorDeviceClass.BATTERY
    assert _attr(EVSocPercentSensor, "native_unit_of_measurement") == "%"
    assert _attr(EVSocPercentSensor, "icon") == "mdi:battery-charging"
    assert _attr(EVSocPercentSensor, "state_class") == SensorStateClass.MEASUREMENT
    assert _attr(EVSocPercentSensor, "suggested_display_precision") == 0


def test_time_to_target_soc_sensor_metadata() -> None:
    assert TimeToTargetSocSensor.ENTITY_NAME == "Time to Target SOC"
    assert _attr(TimeToTargetSocSensor, "icon") == "mdi:timer"


def test_energy_to_target_soc_sensor_metadata() -> None:
    from custom_components.eveus.ev_sensors import EnergyToTargetSocSensor
    from homeassistant.components.sensor import SensorStateClass
    from homeassistant.const import UnitOfEnergy

    assert EnergyToTargetSocSensor.ENTITY_NAME == "Energy to Target SOC"
    assert _attr(EnergyToTargetSocSensor, "native_unit_of_measurement") == UnitOfEnergy.KILO_WATT_HOUR
    assert _attr(EnergyToTargetSocSensor, "icon") == "mdi:battery-arrow-up"
    assert _attr(EnergyToTargetSocSensor, "state_class") == SensorStateClass.MEASUREMENT
    assert _attr(EnergyToTargetSocSensor, "suggested_display_precision") == 1


def test_cost_to_target_soc_sensor_metadata() -> None:
    from custom_components.eveus.ev_sensors import CostToTargetSocSensor

    assert CostToTargetSocSensor.ENTITY_NAME == "Cost to Target SOC"
    assert _attr(CostToTargetSocSensor, "icon") == "mdi:cash-clock"
    assert _attr(CostToTargetSocSensor, "suggested_display_precision") == 0


def test_charging_finish_time_sensor_metadata() -> None:
    from homeassistant.components.sensor import SensorDeviceClass

    assert ChargingFinishTimeSensor.ENTITY_NAME == "Charging Finish Time"
    assert _attr(ChargingFinishTimeSensor, "device_class") == SensorDeviceClass.TIMESTAMP
    assert _attr(ChargingFinishTimeSensor, "icon") == "mdi:calendar-clock"


# =============================================================================
# Write-condition boolean logic in _on_soc_input_changed / _handle_coordinator_update
# =============================================================================


def _controlled_sensor(*, updater_available: bool = True, last_update_success: bool = True):
    """An EVSocKwhSensor (helpers not required) whose sub-update hooks and
    write calls can be fully controlled/observed by the caller.

    Because _requires_helpers is False, `sensor.available` tracks only
    `_entity_available`, so it stays constant across a call unless a
    monkeypatched hook mutates `_entity_available` itself — isolating the
    availability_changed/previous_available-vs-self.available booleans from
    each other for precise testing.
    """
    calculator = push_helpers(CachedSOCCalculator(), EV_HELPERS)
    updater = EveusTestUpdater({"sessionEnergy": "10"}, available=updater_available)
    updater.last_update_success = last_update_success
    sensor = EVSocKwhSensor(updater, 1, calculator)
    sensor.hass = HelperHass(EV_HELPERS)
    writes: list[int] = []
    sensor.async_write_ha_state = lambda: writes.append(1)
    return sensor, writes


@pytest.mark.parametrize("method_name", ["_on_soc_input_changed", "_handle_coordinator_update"])
def test_write_fires_when_availability_changed_flag_alone_is_true(method_name: str) -> None:
    """availability_changed=True must trigger a write even when nothing else
    (including the previous/current .available comparison) changed."""
    sensor, writes = _controlled_sensor()
    sensor._update_availability_state = lambda **kw: True
    sensor._update_native_value = lambda: False
    sensor._update_extra_state_attributes = lambda: False

    getattr(sensor, method_name)()

    assert writes == [1]


@pytest.mark.parametrize("method_name", ["_on_soc_input_changed", "_handle_coordinator_update"])
def test_write_is_quiet_when_nothing_reports_a_change(method_name: str) -> None:
    """All-False sub-signals with no actual availability change: no write."""
    sensor, writes = _controlled_sensor()
    sensor._update_availability_state = lambda **kw: False
    sensor._update_native_value = lambda: False
    sensor._update_extra_state_attributes = lambda: False

    getattr(sensor, method_name)()

    assert writes == []


@pytest.mark.parametrize("method_name", ["_on_soc_input_changed", "_handle_coordinator_update"])
def test_stale_refresh_write_uses_or_of_availability_signals(method_name: str) -> None:
    """Inside the stale (updater unavailable / failed) branch, a write must
    fire on EITHER availability_changed being True OR the property actually
    differing — never requiring both (rules out `and`) and never firing
    merely because the two happen to already be equal (rules out `==`)."""
    # Stale branch: updater unavailable AND last_update_success False, so a
    # single-flag mutation of the guard's `or`->`and` doesn't also mask this.
    sensor, writes = _controlled_sensor(updater_available=False, last_update_success=False)
    sensor._update_availability_state = lambda **kw: True
    sensor._update_native_value = lambda: False
    sensor._update_extra_state_attributes = lambda: False

    getattr(sensor, method_name)()

    assert writes == [1]

    # Second call: availability_changed False and no property change -> quiet.
    sensor2, writes2 = _controlled_sensor(updater_available=False, last_update_success=False)
    sensor2._update_availability_state = lambda **kw: False
    sensor2._update_native_value = lambda: False
    sensor2._update_extra_state_attributes = lambda: False

    getattr(sensor2, method_name)()

    assert writes2 == []


@pytest.mark.parametrize("method_name", ["_on_soc_input_changed", "_handle_coordinator_update"])
def test_stale_guard_treats_available_and_success_as_independent_or(method_name: str) -> None:
    """Either the updater being unavailable OR a failed last update alone
    must select the stale (no-recompute) branch — not require both."""
    # available=True but last_update_success=False: still stale.
    sensor, _writes = _controlled_sensor(updater_available=True, last_update_success=False)
    sensor._update_availability_state = lambda **kw: False

    def _boom():
        raise AssertionError(f"{method_name} recomputed value on a stale refresh")

    sensor._update_native_value = _boom
    sensor._update_extra_state_attributes = _boom

    getattr(sensor, method_name)()  # must not raise


def test_on_soc_input_changed_recomputes_value_and_attributes_when_fresh() -> None:
    """Non-stale branch: value_changed/attributes_changed alone must each be
    able to trigger a write (attributes_changed=None mutation guard)."""
    sensor, writes = _controlled_sensor()
    sensor._update_availability_state = lambda **kw: False
    sensor._update_native_value = lambda: False
    sensor._update_extra_state_attributes = lambda: True

    sensor._on_soc_input_changed()

    assert writes == [1]


def test_handle_coordinator_update_recomputes_attributes_when_fresh() -> None:
    sensor, writes = _controlled_sensor()
    sensor._update_availability_state = lambda **kw: False
    sensor._update_native_value = lambda: False
    sensor._update_extra_state_attributes = lambda: True

    sensor._handle_coordinator_update()

    assert writes == [1]


def test_base_ev_helper_sensor_class_default_requires_helpers() -> None:
    """The class-level default (before any subclass override) is True."""
    assert BaseEVHelperSensor._requires_helpers is True


def test_ev_helper_sensor_defaults_to_device_number_one() -> None:
    """Omitting device_number must build a device-1 unique_id, not device-2+."""
    sensor = EVSocKwhSensor(EveusTestUpdater({"sessionEnergy": "10"}))

    assert sensor.unique_id == "eveus_soc_energy"


def test_ev_helper_sensor_starts_with_no_cached_value() -> None:
    """The cache must start as None (unset), not a placeholder value."""
    sensor = EVSocKwhSensor(EveusTestUpdater({"sessionEnergy": "10"}))

    assert sensor._cached_value is None


def test_available_is_false_when_base_entity_is_unavailable_regardless_of_helpers() -> None:
    """A sensor that doesn't require helpers must still go unavailable when
    the base (connection-level) availability is False."""
    calculator = push_helpers(CachedSOCCalculator(), EV_HELPERS)
    sensor = EVSocKwhSensor(EveusTestUpdater({"sessionEnergy": "10"}), 1, calculator)
    sensor._entity_available = False

    assert sensor.available is False


def test_ev_helper_sensors_do_not_require_helpers_except_base_default() -> None:
    """Only the base class defaults to requiring helpers; every concrete EV
    sensor overrides it to False (each has its own unknown-when-missing
    fallback instead of going fully unavailable)."""
    from custom_components.eveus.ev_sensors import (
        BaseEVHelperSensor,
        EnergyToTargetSocSensor,
    )

    assert BaseEVHelperSensor._requires_helpers is True
    assert EVSocKwhSensor._requires_helpers is False
    assert EVSocPercentSensor._requires_helpers is False
    assert TimeToTargetSocSensor._requires_helpers is False
    assert EnergyToTargetSocSensor._requires_helpers is False
    assert ChargingFinishTimeSensor._requires_helpers is False


def test_soc_percent_sensor_reports_how_it_was_anchored() -> None:
    """The user must be able to see whether Initial SOC was seeded or set by hand.

    Every SOC figure derives from Initial SOC. A stale hand-set value, or a
    failed external-sensor seed, silently poisons the reading and was until now
    only visible inside a downloaded diagnostics file.
    """
    calculator = push_helpers(CachedSOCCalculator(), EV_HELPERS)
    updater = EveusTestUpdater({"sessionEnergy": "16"})
    sensor = EVSocPercentSensor(updater, 1, calculator)

    calculator.last_seed = {"seeded": True, "detail": "62.0% from sensor.car_battery"}
    assert sensor._update_extra_state_attributes() is True
    assert sensor.extra_state_attributes["soc_anchor"] == "62.0% from sensor.car_battery"
    assert sensor.extra_state_attributes["soc_anchor_seeded"] is True

    # Recorder hygiene: an unchanged anchor must NOT report a change, or the
    # sensor writes a database row on every poll.
    assert sensor._update_extra_state_attributes() is False

    calculator.last_seed = {"seeded": False, "detail": "reading is unavailable"}
    assert sensor._update_extra_state_attributes() is True
    assert sensor.extra_state_attributes["soc_anchor"] == "reading is unavailable"
    assert sensor.extra_state_attributes["soc_anchor_seeded"] is False

    calculator.last_seed = {}
    assert sensor._update_extra_state_attributes() is True
    assert sensor.extra_state_attributes["soc_anchor"] == "set manually"
    assert sensor.extra_state_attributes["soc_anchor_seeded"] is False
