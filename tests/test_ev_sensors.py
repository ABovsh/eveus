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
    first = EVSocKwhSensor(_Updater({"sessionEnergy": "10"}), 1, first_calculator)
    second = EVSocKwhSensor(_Updater({"sessionEnergy": "10"}), 2, second_calculator)

    assert first._soc_calculator is first_calculator
    assert second._soc_calculator is second_calculator
    assert first._soc_calculator is not second._soc_calculator


def test_time_to_target_soc_uses_shared_calculator_cache() -> None:
    calculator = CachedSOCCalculator()
    sensor = TimeToTargetSocSensor(
        _Updater({"sessionEnergy": "0", "powerMeas": "7000"}),
        1,
        calculator,
    )
    sensor.hass = _Hass(HELPERS)

    assert sensor._get_sensor_value() == "7h 37m"
    assert calculator.battery_capacity == 80
    assert calculator.target_soc == 80


def test_cached_soc_calculator_exposes_all_cached_properties() -> None:
    """All four CachedSOCCalculator properties return the helper values after cache warm."""
    calculator = CachedSOCCalculator()

    calculator._update_input_cache(_Hass(HELPERS))

    assert calculator.battery_capacity == 80
    assert calculator.initial_soc == 20
    assert calculator.soc_correction == 10
    assert calculator.target_soc == 80


def test_cached_soc_calculator_invalidate_clears_cached_values() -> None:
    """invalidate_cache forces a re-read on the next access."""
    calculator = CachedSOCCalculator()
    calculator._update_input_cache(_Hass(HELPERS))
    assert calculator.battery_capacity == 80

    calculator.invalidate_cache()

    updated = dict(HELPERS)
    updated["input_number.ev_battery_capacity"] = 60
    calculator._update_input_cache(_Hass(updated))

    assert calculator.battery_capacity == 60


def test_energy_charged_reads_session_energy_directly() -> None:
    """4.6.0: energy delivered comes from the charger's `sessionEnergy` field."""
    calculator = CachedSOCCalculator()
    sensor = EVSocKwhSensor(_Updater({"sessionEnergy": "12.5"}), 1, calculator)
    sensor.hass = _Hass(HELPERS)

    assert sensor._get_energy_charged() == 12.5


def test_energy_charged_returns_none_when_session_energy_missing() -> None:
    calculator = CachedSOCCalculator()
    sensor = EVSocKwhSensor(_Updater({}), 1, calculator)
    sensor.hass = _Hass(HELPERS)

    assert sensor._get_energy_charged() is None


def test_session_reset_collapses_to_zero() -> None:
    """When the charger starts a new session it resets sessionEnergy itself —
    we must reflect that immediately, not carry a stale baseline."""
    calculator = CachedSOCCalculator()
    updater = _Updater({"sessionEnergy": "8"})
    sensor = EVSocKwhSensor(updater, 1, calculator)
    sensor.hass = _Hass(HELPERS)

    assert sensor._get_energy_charged() == 8.0
    updater.data = {"sessionEnergy": "0"}
    assert sensor._get_energy_charged() == 0.0


def test_calculator_has_no_baseline_state() -> None:
    """4.6.0 removed energy_baseline / baseline_initial_soc / restore_baseline."""
    calc = CachedSOCCalculator()
    assert not hasattr(calc, "energy_baseline")
    assert not hasattr(calc, "baseline_initial_soc")
    assert not hasattr(calc, "restore_baseline")


def test_soc_kwh_sensor_uses_measurement_state_class() -> None:
    # Regression: TOTAL without last_reset breaks HA statistics.
    # SOC kWh is a running gauge (not a monotonic lifetime counter).
    # HA's CachedProperties metaclass stores default attr values under __attr_* keys.
    from homeassistant.components.sensor import SensorStateClass
    default_state_class = vars(EVSocKwhSensor).get("__attr_state_class")
    assert default_state_class == SensorStateClass.MEASUREMENT, (
        f"EVSocKwhSensor._attr_state_class should be MEASUREMENT, got {default_state_class!r}"
    )
