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


def test_get_energy_charged_warms_cache_via_update_input_cache() -> None:
    """_get_energy_charged must call _update_input_cache (not are_helpers_available)
    so that initial_soc is fresh before baseline detection runs."""
    calculator = CachedSOCCalculator()
    sensor = EVSocKwhSensor(_Updater({"IEM1": "25"}), 1, calculator)
    sensor.hass = _Hass(HELPERS)

    # Simulate a direct call to _get_energy_charged — cache must be warmed.
    result = sensor._get_energy_charged()

    # initial_soc must be populated from helpers.
    assert calculator.initial_soc == 20
    # Energy baseline should be anchored at 25 (first read).
    assert result == 0.0


def test_energy_baseline_survives_helper_blip() -> None:
    """A transient None initial_soc must not reset the energy baseline.

    Reproduces the regression where helpers going briefly unavailable caused
    session energy to collapse to 0 the moment the helpers came back.
    """
    calculator = CachedSOCCalculator()
    updater = _Updater({"IEM1": "25"})
    sensor = EVSocKwhSensor(updater, 1, calculator)
    sensor.hass = _Hass(HELPERS)

    # Initial read anchors the baseline at 25.
    assert sensor._get_energy_charged() == 0.0

    # 5 kWh charged with helpers present.
    updater.data = {"IEM1": "30"}
    assert sensor._get_energy_charged() == 5.0

    # Helpers blip: initial_soc becomes None for the next read.
    sensor.hass = _Hass({})
    calculator.invalidate_cache()
    blip_result = sensor._get_energy_charged()
    # Baseline must NOT reset on a None initial_soc — running total preserved.
    assert blip_result == 5.0

    # Helpers come back with the same value: still no reset.
    sensor.hass = _Hass(HELPERS)
    calculator.invalidate_cache()
    assert sensor._get_energy_charged() == 5.0


def test_baseline_persists_via_extra_state_attributes_and_restore() -> None:
    """Baseline survives a HA restart via EVSocKwhSensor state attributes.

    Regression: before 4.5.1 the baseline lived only in RAM on the sensor
    instance, so after restart the first IEM1 read became a new baseline
    and delivered energy snapped to 0 (SoC fell back to initial_soc).
    """
    # --- Session-1: anchor baseline at 25, deliver 5 kWh.
    calc1 = CachedSOCCalculator()
    updater1 = _Updater({"IEM1": "25"})
    sensor1 = EVSocKwhSensor(updater1, 1, calc1)
    sensor1.hass = _Hass(HELPERS)

    assert sensor1._get_energy_charged() == 0.0
    updater1.data = {"IEM1": "30"}
    assert sensor1._get_energy_charged() == 5.0

    # Persisted attributes that HA's RestoreEntity would write to disk.
    sensor1._update_extra_state_attributes()
    persisted = dict(sensor1._attr_extra_state_attributes)
    assert persisted["energy_baseline_kwh"] == 25.0
    assert persisted["baseline_initial_soc"] == 20.0

    # --- Session-2: simulate HA restart — fresh calculator + sensor.
    calc2 = CachedSOCCalculator()
    updater2 = _Updater({"IEM1": "30"})
    sensor2 = EVSocKwhSensor(updater2, 1, calc2)
    sensor2.hass = _Hass(HELPERS)

    # async_added_to_hass would call _async_restore_state with the last state.
    import asyncio
    asyncio.run(
        sensor2._async_restore_state(SimpleNamespace(state="5.0", attributes=persisted))
    )

    # First read after restart: IEM1 still 30, baseline restored to 25 → still 5 kWh.
    assert sensor2._get_energy_charged() == 5.0
    # And SoC math sees the same delivered energy.
    assert calc2.energy_baseline == 25.0
    assert calc2.baseline_initial_soc == 20.0


def test_restore_baseline_is_noop_when_baseline_already_set() -> None:
    """restore_baseline must not overwrite a live baseline."""
    calc = CachedSOCCalculator()
    calc._energy_baseline = 10.0
    calc._baseline_initial_soc = 50.0

    calc.restore_baseline(99.0, 99.0)

    assert calc.energy_baseline == 10.0
    assert calc.baseline_initial_soc == 50.0


def test_restore_baseline_handles_missing_attributes() -> None:
    """No persisted attrs (pre-4.5.1 install) → no crash, no baseline seeded."""
    calc = CachedSOCCalculator()

    calc.restore_baseline(None, None)

    assert calc.energy_baseline is None
    assert calc.baseline_initial_soc is None


def test_baseline_shared_across_sensors_on_same_device() -> None:
    """All helper sensors on one device must share the baseline via the calculator."""
    calc = CachedSOCCalculator()
    updater = _Updater({"IEM1": "25"})
    kwh_sensor = EVSocKwhSensor(updater, 1, calc)
    kwh_sensor.hass = _Hass(HELPERS)

    # Anchor baseline via the kWh sensor.
    assert kwh_sensor._get_energy_charged() == 0.0
    assert calc.energy_baseline == 25.0

    # A second helper sensor sharing the same calculator sees the same baseline.
    second = EVSocKwhSensor(_Updater({"IEM1": "32"}), 1, calc)
    second.hass = _Hass(HELPERS)
    assert second._get_energy_charged() == 7.0


def test_soc_kwh_sensor_uses_measurement_state_class() -> None:
    # Regression: TOTAL without last_reset breaks HA statistics.
    # SOC kWh is a running gauge (not a monotonic lifetime counter).
    # HA's CachedProperties metaclass stores default attr values under __attr_* keys.
    from homeassistant.components.sensor import SensorStateClass
    default_state_class = vars(EVSocKwhSensor).get("__attr_state_class")
    assert default_state_class == SensorStateClass.MEASUREMENT, (
        f"EVSocKwhSensor._attr_state_class should be MEASUREMENT, got {default_state_class!r}"
    )
