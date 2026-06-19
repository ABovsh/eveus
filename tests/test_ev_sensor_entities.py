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


def test_soc_sensors_keep_cached_value_when_inputs_temporarily_missing() -> None:
    calculator = push_helpers(CachedSOCCalculator(), EV_HELPERS)
    updater = EveusTestUpdater({"sessionEnergy": "16"})
    kwh = EVSocKwhSensor(updater, 1, calculator)
    percent = EVSocPercentSensor(updater, 1, calculator)

    assert kwh._get_sensor_value() == pytest.approx(30.4)
    assert percent._get_sensor_value() == 38
    calculator.set_value("battery_capacity", None)

    assert kwh._get_sensor_value() == pytest.approx(30.4)
    assert percent._get_sensor_value() == 38


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


# ---------------------------------------------------------------------------
# From test_rc5_hardening.py — F17 time_to_target resets cache
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

    # Inputs unavailable → must reset to unknown, not keep showing "2h 15m".
    assert sensor._get_sensor_value() is None
    assert sensor._cached_value is None


# ---------------------------------------------------------------------------
# From test_rc9_hardening.py — I2 time-to-target unknown, O1 soc_percent derivation
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# From test_rc13_hardening.py — R2-1 SOC/ETA reject finite outliers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# From test_hardening_4_10_0.py — F11 SOC ETA unknown when telemetry missing
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# From test_rc15_hardening.py — F22 no ETA outside active session
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# From test_rc16_hardening.py — A06 energy computed in kWh not percent, A11/A12 cost
# ---------------------------------------------------------------------------

import pytest as _pytest


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
    assert value == _pytest.approx(0.49, abs=0.02)


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


# ---------------------------------------------------------------------------
# From test_rc17_hardening.py — V-11 exact SOC percent
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# From test_rc6_hardening.py — F02 SOC correction preserved/defaulted
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# From test_rc6_hardening.py — F03 session energy present vs absent
# ---------------------------------------------------------------------------

def test_session_energy_invalid_when_present_and_negative() -> None:
    from custom_components.eveus import ev_sensors

    sensor = ev_sensors.EVSocKwhSensor(EveusTestUpdater({"sessionEnergy": -1.0}))
    assert sensor._session_energy_is_invalid() is True


def test_session_energy_not_invalid_when_absent() -> None:
    from custom_components.eveus import ev_sensors

    sensor = ev_sensors.EVSocKwhSensor(EveusTestUpdater({}))
    assert sensor._session_energy_is_invalid() is False


# ---------------------------------------------------------------------------
# From test_privacy_and_soc_hardening.py — SOC input validation
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# From test_rc16_hardening.py — energy-to-target sensor
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# From test_rc16_features.py — energy-to-target and cost-to-target
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# From test_rc16_features.py — cost to target without tariff / rate2
# ---------------------------------------------------------------------------

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
