"""Unit tests for generated sensor value definitions."""
from __future__ import annotations

from types import SimpleNamespace

from homeassistant.helpers.entity import EntityCategory

from custom_components.eveus import sensor_definitions as sensors


def _updater(data: dict[str, object], *, available: bool = True) -> SimpleNamespace:
    return SimpleNamespace(data=data, available=available, connection_quality={})


def test_measurement_getters_convert_device_payload_values() -> None:
    updater = _updater(
        {
            "voltMeas1": "229.6",
            "curMeas1": "14.24",
            "powerMeas": "3265.55",
            "currentSet": "16",
        }
    )

    assert sensors.get_voltage(updater, None) == 230
    assert sensors.get_current(updater, None) == 14.2
    assert sensors.get_power(updater, None) == 3265.6
    assert sensors.get_current_set(updater, None) == 16


def test_state_getters_map_known_values() -> None:
    updater = _updater({"state": "4", "subState": "1", "ground": "1"})

    assert sensors.get_charger_state(updater, None) == "Charging"
    assert sensors.get_charger_substate(updater, None) == "Limited by User"
    assert sensors.get_ground_status(updater, None) == "Connected"


def test_error_state_uses_error_mapping() -> None:
    updater = _updater({"state": "7", "subState": "10"})

    assert sensors.get_charger_substate(updater, None) == "Overcurrent"


def test_rate_costs_are_converted_from_cents() -> None:
    updater = _updater(
        {
            "activeTarif": "1",
            "tarif": "264",
            "tarifAValue": "132",
            "tarifBValue": "400",
            "tarifAEnable": "1",
            "tarifBEnable": "0",
        }
    )

    assert sensors.get_primary_rate_cost(updater, None) == 2.64
    assert sensors.get_rate2_cost(updater, None) == 1.32
    assert sensors.get_rate3_cost(updater, None) == 4.0
    assert sensors.get_active_rate_cost(updater, None) == 1.32
    assert sensors._make_rate_status_getter("tarifAEnable")(updater, None) == "Enabled"
    assert sensors._make_rate_status_getter("tarifBEnable")(updater, None) == "Disabled"


def test_getters_return_none_when_updater_is_unavailable() -> None:
    updater = _updater({"powerMeas": "1200"}, available=False)

    assert sensors.get_power(updater, None) is None
    assert sensors.get_charger_state(updater, None) is None


def test_sensor_specification_factory_exposes_expected_entities() -> None:
    specs = sensors.get_sensor_specifications()
    names = {spec.name for spec in specs}

    # Spot-check entities from each section so a silent drop of any of these
    # families is caught — not just "shape" coverage.
    assert "Voltage" in names
    assert "Session Energy" in names
    assert "State" in names
    assert "Connection Quality" in names
    assert "Session Cost" in names  # back as a SensorSpec in 4.6.0
    assert "Leakage Current" in names  # 4.9.0-rc.2
    assert "Leakage Current Peak" in names  # 4.9.0-rc.2
    # Exact count: catches silent additions/removals; bump on intentional
    # changes alongside README/CHANGELOG.
    # 4.7.0: +5 adaptive/scheduled-charging sensors (Adaptive Charging,
    # Adaptive Current Limit, Adaptive Voltage Threshold, Schedule 1, Schedule 2).
    # 4.9.0-rc.2: +2 leakage sensors (always on).
    assert len(specs) == 33, sorted(names)


def test_sensor_specifications_adds_three_phase_sensors_when_requested() -> None:
    one_phase = {s.name for s in sensors.get_sensor_specifications(phases=1)}
    three_phase = {s.name for s in sensors.get_sensor_specifications(phases=3)}
    new_in_three = three_phase - one_phase
    assert new_in_three == {
        "Current Phase 2",
        "Current Phase 3",
        "Voltage Phase 2",
        "Voltage Phase 3",
    }


def test_value_getters_reject_nan_and_inf() -> None:
    """Regression: float() accepts 'nan'/'inf' but those are not valid readings.
    They must be filtered to None so HA doesn't store nonsense in long-term
    statistics or compute downstream cost/finish-time off bad inputs.
    """
    updater = SimpleNamespace(
        data={"voltMeas1": "nan", "powerMeas": "inf", "sessionEnergy": "-inf"},
        available=True,
        connection_quality={},
    )
    assert sensors.get_voltage(updater, None) is None
    assert sensors.get_power(updater, None) is None
    assert sensors.get_session_energy(updater, None) is None


def test_status_like_entities_are_diagnostic() -> None:
    specs = {spec.name: spec for spec in sensors.get_sensor_specifications()}

    assert specs["Current Set"].category == EntityCategory.DIAGNOSTIC
    assert specs["Rate 2 Status"].category == EntityCategory.DIAGNOSTIC
    assert specs["Rate 3 Status"].category == EntityCategory.DIAGNOSTIC


def test_session_energy_uses_measurement_state_class() -> None:
    # Regression: TOTAL without last_reset breaks HA long-term energy statistics.
    # Session energy resets each session (MEASUREMENT), not a monotonic counter.
    specs = {spec.name: spec for spec in sensors.get_sensor_specifications()}
    assert specs["Session Energy"].state_class == "measurement"


def test_sensor_keys_and_names_are_unique() -> None:
    specs = sensors.get_sensor_specifications()
    keys = [s.key for s in specs]
    names = [s.name for s in specs]
    assert len(keys) == len(set(keys)), f"Duplicate keys: {[k for k in keys if keys.count(k) > 1]}"
    assert len(names) == len(set(names)), f"Duplicate names: {[n for n in names if names.count(n) > 1]}"


def test_monotonic_energy_sensors_use_total_increasing() -> None:
    specs = {spec.name: spec for spec in sensors.get_sensor_specifications()}
    for name in ("Total Energy", "Counter A Energy", "Counter B Energy"):
        assert specs[name].state_class == "total_increasing", f"{name} should be TOTAL_INCREASING"


def test_connection_attrs_returns_quantized_numerics_not_drifting_strings() -> None:
    """Connection attrs must be quantized numeric values to avoid per-tick state writes."""
    from custom_components.eveus.sensor_definitions import get_connection_attrs

    class _Fake:
        available = True
        connection_quality = {"success_rate": 99.34, "latency_avg": 0.873}

    attrs = get_connection_attrs(_Fake(), None)
    assert attrs["connection_quality"] == 99
    assert attrs["latency_avg"] == 1.0  # rounded to nearest 0.5
    assert isinstance(attrs["connection_quality"], int)
    assert isinstance(attrs["latency_avg"], float)
