"""Unit tests for generated sensor value definitions."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from homeassistant.helpers.entity import EntityCategory

from custom_components.eveus import sensor_definitions as sensors


def _updater(data: dict[str, object], *, available: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        data=data,
        available=available,
        connection_quality={},
        host="192.168.1.50",
    )


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
    assert sensors.get_current(updater, None) == pytest.approx(14.2)
    assert sensors.get_power(updater, None) == pytest.approx(3265.6)
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

    assert sensors.get_primary_rate_cost(updater, None) == pytest.approx(2.64)
    assert sensors.get_rate2_cost(updater, None) == pytest.approx(1.32)
    assert sensors.get_rate3_cost(updater, None) == pytest.approx(4.0)
    assert sensors.get_active_rate_cost(updater, None) == pytest.approx(1.32)
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
    assert "Leakage Current" in names
    assert "Leakage Current Peak" in names
    # Exact count: catches silent additions/removals; bump on intentional
    # changes alongside README/CHANGELOG.
    # 4.7.0: +5 adaptive/scheduled-charging sensors (Adaptive Charging,
    # Adaptive Current Limit, Adaptive Voltage Threshold, Schedule 1, Schedule 2).
    # Leakage sensors are always exposed.
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


def test_duplicate_sensor_keys_raise_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    original_spec = sensors.SensorSpec

    def duplicate_key_spec(*args, **kwargs):
        kwargs["key"] = "duplicate"
        return original_spec(*args, **kwargs)

    monkeypatch.setattr(sensors, "SensorSpec", duplicate_key_spec)

    with pytest.raises(RuntimeError, match="duplicate sensor keys"):
        sensors.create_sensor_specifications()


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


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({}, None),
        ({"powerMeas": None}, None),
        ({"powerMeas": True}, None),
        ({"powerMeas": "bad"}, None),
        ({"powerMeas": "-1"}, None),
        ({"powerMeas": "12.34"}, 12.3),
    ],
)
def test_value_getter_rejects_invalid_payload_shapes(
    data: dict[str, object], expected: float | None
) -> None:
    assert sensors.get_power(_updater(data), None) == expected


def test_value_getter_applies_transform_after_validation() -> None:
    updater = _updater({"tarif": "123"})

    assert sensors.get_primary_rate_cost(updater, None) == pytest.approx(1.23)


def test_state_getters_return_none_for_unknown_codes_and_missing_values() -> None:
    assert sensors.get_ground_status(_updater({"ground": "3"}), None) is None
    assert sensors.get_charger_substate(_updater({"state": "4"}), None) is None
    assert sensors.get_charger_substate(_updater({"subState": "1"}), None) is None
    assert sensors._make_rate_status_getter("tarifAEnable")(
        _updater({"tarifAEnable": "2"}), None
    ) is None


def test_session_time_and_active_rate_attributes_handle_edge_cases() -> None:
    assert sensors.get_session_time(_updater({"sessionTime": "3661"}), None) == "1h 01m"
    assert sensors.get_session_time_attrs(_updater({"sessionTime": "61"}), None) == {
        "duration_seconds": 61
    }
    assert sensors.get_session_time_attrs(_updater({}, available=False), None) == {}
    assert sensors.get_session_time_attrs(_updater({"sessionTime": "bad"}), None) == {}

    assert sensors.get_active_rate_cost(_updater({"activeTarif": "5"}), None) is None
    assert sensors.get_active_rate_cost(_updater({"activeTarif": "2"}), None) is None
    assert sensors.get_active_rate_attrs(_updater({"activeTarif": "9"}), None) == {
        "rate_name": "Unknown"
    }
    assert sensors.get_active_rate_attrs(_updater({}, available=False), None) == {}


def test_adaptive_and_schedule_helpers_cover_invalid_and_cap_paths() -> None:
    assert sensors.get_adaptive_charging_state(_updater({"aiStatus": "1"}), None) == "Active"
    assert sensors.get_adaptive_charging_state(_updater({"aiStatus": "0"}), None) == "Idle"
    assert sensors.get_adaptive_charging_state(_updater({"aiStatus": "2"}), None) is None

    schedule = sensors._make_schedule_getter(1)
    attrs = sensors._make_schedule_attrs(1)
    updater = _updater(
        {
            "sh1Enabled": "1",
            "sh1Start": "60",
            "sh1Stop": "1439",
            "sh1CurrentEnable": "1",
            "sh1CurrentValue": "12",
            "sh1EnergyEnable": "1",
            "sh1EnergyValue": "8.5",
        }
    )

    assert schedule(updater, None) == "Enabled"
    assert attrs(updater, None) == {
        "window": "01:00–23:59",
        "start": "01:00",
        "stop": "23:59",
        "current_limit_a": 12,
        "energy_limit_kwh": 8.5,
    }
    assert schedule(_updater({"sh1Enabled": "0"}), None) == "Disabled"
    assert schedule(_updater({"sh1Enabled": "2"}), None) is None
    assert attrs(_updater({"sh1Start": "-1", "sh1Stop": "1440"}), None) == {}
    assert attrs(_updater({}, available=False), None) == {}


@pytest.mark.parametrize(
    ("success_rate", "expected_status"),
    [
        (96, "Excellent"),
        (81, "Good"),
        (61, "Fair"),
        (31, "Poor"),
        (30, "Critical"),
    ],
)
def test_connection_attrs_status_bands(
    success_rate: int, expected_status: str
) -> None:
    updater = SimpleNamespace(
        available=True,
        data={},
        connection_quality={"success_rate": success_rate, "latency_avg": -1},
    )

    assert sensors.get_connection_attrs(updater, None) == {
        "connection_quality": success_rate,
        "latency_avg": 0.0,
        "status": expected_status,
    }


@pytest.mark.parametrize("rate", [True, "99", float("nan")])
def test_connection_quality_rejects_invalid_rates(rate: object) -> None:
    updater = SimpleNamespace(available=True, data={}, connection_quality={"success_rate": rate})

    assert sensors.get_connection_quality(updater, None) is None


def test_connection_quality_clamps_and_handles_metric_errors() -> None:
    assert sensors.get_connection_quality(
        SimpleNamespace(available=True, data={}, connection_quality={"success_rate": 150}),
        None,
    ) == 100
    assert sensors.get_connection_quality(
        SimpleNamespace(available=True, data={}, connection_quality={"success_rate": -5}),
        None,
    ) == 0

    class BrokenMetrics:
        @property
        def connection_quality(self):
            raise RuntimeError("no metrics")

    assert sensors.get_connection_quality(BrokenMetrics(), None) is None
    assert sensors.get_connection_attrs(BrokenMetrics(), None) == {"status": "Error"}


def test_system_time_handles_invalid_timestamp_without_raising() -> None:
    assert sensors.get_system_time(_updater({"systemTime": "bad"}), None) is None


def test_system_time_handles_data_access_exception_without_raising() -> None:
    class BrokenUpdater:
        available = True

        @property
        def data(self):
            raise RuntimeError("data unavailable")

    assert sensors.get_system_time(BrokenUpdater(), None) is None


def test_optimized_sensor_contract_for_offline_and_attribute_errors() -> None:
    updater = _updater({"value": "1"}, available=False)
    spec = sensors.SensorSpec(
        key="contract",
        name="Contract",
        value_fn=lambda updater, hass: 1,
        sensor_type=sensors.SensorType.DIAGNOSTIC,
        icon="mdi:test-tube",
        device_class="custom",
        state_class="measurement",
        unit="x",
        precision=1,
        category=EntityCategory.DIAGNOSTIC,
        attributes_fn=lambda updater, hass: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    sensor = spec.create_sensor(updater)

    assert sensor._get_sensor_value() is None
    assert sensor._update_extra_state_attributes() is False
    assert sensor.extra_state_attributes == {}
    assert sensor.icon == "mdi:test-tube"
    assert sensor.device_class == "custom"
    assert sensor.state_class == "measurement"
    assert sensor.native_unit_of_measurement == "x"
    assert sensor.suggested_display_precision == 1
    assert sensor.entity_category == EntityCategory.DIAGNOSTIC

    updater.available = True
    assert sensor._update_extra_state_attributes() is False
