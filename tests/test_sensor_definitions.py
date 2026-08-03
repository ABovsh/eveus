"""Unit tests for generated sensor value definitions."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from conftest import spec_value_fn
from homeassistant.helpers.entity import EntityCategory

from custom_components.eveus import sensor_definitions as sd
from custom_components.eveus import sensor_definitions as sensors


def _updater(data: dict[str, object], *, available: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        data=data,
        available=available,
        connection_quality={},
        host="192.168.1.50",
    )


@pytest.fixture
def make_updater():
    return _updater


def test_enum_getter_maps_values(make_updater):
    g = sd._make_enum_getter("sh1Enabled", {1: "Enabled", 0: "Disabled"})
    assert g(make_updater({"sh1Enabled": 1}), None) == "Enabled"
    assert g(make_updater({"sh1Enabled": 0}), None) == "Disabled"
    assert g(make_updater({"sh1Enabled": 9}), None) is None
    assert g(make_updater({}), None) is None


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
    assert spec_value_fn("current_set")(updater, None) == 16


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
    # 4.7.0: +5 adaptive/scheduled-charging sensors.
    # 4.9.2-rc2: +2 diagnostic sensors (WiFi Signal, Control Pilot).
    # 4.9.2-rc5: Control Pilot removed (jargon; misled users).
    assert "WiFi Signal" in names
    assert "Control Pilot" not in names
    assert "Adaptive Voltage Threshold" not in names
    assert "Not Charging Reason" in names
    assert len(specs) == 34, sorted(names)


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
    # Derived from State + Substate, and shown next to them under Diagnostic.
    assert specs["Not Charging Reason"].category == EntityCategory.DIAGNOSTIC


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


def test_duplicate_sensor_keys_error_lists_only_the_actual_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard's message must name exactly the keys that collided — not
    every key (an off-by-one in the `.count(k) > 1` filter), not none of
    them, and not `None`. A silent wrong-message here would let a real
    duplicate slip through code review undiagnosed."""
    original_spec = sensors.SensorSpec
    calls = {"n": 0}

    def duplicate_first_two_spec(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            kwargs["key"] = "duplicate"
        return original_spec(*args, **kwargs)

    monkeypatch.setattr(sensors, "SensorSpec", duplicate_first_two_spec)

    with pytest.raises(RuntimeError) as exc_info:
        sensors.create_sensor_specifications()

    assert str(exc_info.value) == "duplicate sensor keys: ['duplicate']"


def test_get_sensor_specifications_cache_maxsize_is_eight() -> None:
    assert sensors.get_sensor_specifications.cache_info().maxsize == 8


def test_get_sensor_specifications_is_cached_by_arguments() -> None:
    """Repeated calls with identical arguments must return the *same*
    tuple object, not merely an equal one — that's what `@lru_cache` buys
    us and what removing the decorator would silently break."""
    sensors.get_sensor_specifications.cache_clear()
    first = sensors.get_sensor_specifications(phases=1, max_current=16)
    second = sensors.get_sensor_specifications(phases=1, max_current=16)
    assert first is second


def test_get_sensor_specifications_phases_default_is_one() -> None:
    import inspect

    default = inspect.signature(sensors.get_sensor_specifications).parameters["phases"].default
    assert default == 1


def test_get_sensor_specifications_falls_back_to_model_max_only_when_falsy() -> None:
    """`max_current or _MAX_MODEL_CURRENT`: an explicit truthy max_current
    must be used as-is (not replaced by the global ceiling), and only a
    falsy value (None/0) should fall back. Regression guard for an
    `or`/`and` flip that would silently widen every model's Schedule
    current-limit clamp to the global 48 A ceiling."""
    sensors.get_sensor_specifications.cache_clear()
    specs = {
        s.key: s
        for s in sensors.get_sensor_specifications(phases=1, max_current=16)
    }
    updater = _updater(
        {"sh1CurrentEnable": "1", "sh1CurrentValue": "20"}
    )
    attrs = specs["schedule_1"].attributes_fn(updater, None)
    # 20 A exceeds the explicit max_current=16 clamp, so it must be dropped —
    # not silently accepted because the ceiling widened to _MAX_MODEL_CURRENT.
    assert "current_limit_a" not in attrs
    sensors.get_sensor_specifications.cache_clear()


def test_monotonic_energy_sensors_use_total_increasing() -> None:
    """Both halves of Energy Dashboard compatibility, locked together.

    The dashboard only offers a sensor as an individual device when it is
    ENERGY + TOTAL_INCREASING; losing either class silently drops it from the
    picker and discards its long-term statistics.
    """
    specs = {spec.name: spec for spec in sensors.get_sensor_specifications()}
    for name in ("Total Energy", "Counter A Energy", "Counter B Energy"):
        assert specs[name].state_class == "total_increasing", f"{name} should be TOTAL_INCREASING"
        assert specs[name].device_class == "energy", f"{name} should be ENERGY"


def test_connection_attrs_returns_quantized_numerics_not_drifting_strings() -> None:
    """Connection attrs must be quantized numeric values to avoid per-tick state writes."""
    from custom_components.eveus.sensor_definitions import get_connection_attrs

    class _Fake:
        available = True
        data: dict = {}
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

    assert sensors.get_active_rate_cost(_updater({}), None) is None
    assert sensors.get_active_rate_cost(_updater({"activeTarif": "5"}), None) is None
    assert sensors.get_active_rate_cost(_updater({"activeTarif": "2"}), None) is None
    assert sensors.get_active_rate_attrs(_updater({"activeTarif": "9"}), None) == {
        "rate_name": "Unknown"
    }
    assert sensors.get_active_rate_attrs(_updater({}, available=False), None) == {}


def test_adaptive_and_schedule_helpers_cover_invalid_and_cap_paths() -> None:
    assert sensors.get_adaptive_charging_state(_updater({"aiStatus": "0"}), None) == "Off"
    assert sensors.get_adaptive_charging_state(_updater({"aiStatus": "1"}), None) == "Voltage"
    assert sensors.get_adaptive_charging_state(_updater({"aiStatus": "2"}), None) == "Auto"
    assert sensors.get_adaptive_charging_state(_updater({"aiStatus": "3"}), None) == "Power"

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
    assert attrs(
        _updater(
            {
                "sh1CurrentEnable": "1",
                "sh1CurrentValue": "99",
                "sh1EnergyEnable": "1",
                "sh1EnergyValue": "999",
            }
        ),
        None,
    ) == {}
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


def test_connection_attrs_handles_offline_and_includes_wifi_rssi() -> None:
    # Offline keeps the rolling connectivity attributes (success rate, latency,
    # status update on failed polls and matter most during an outage); only the
    # stale payload-derived wifi_rssi is dropped.
    assert sensors.get_connection_attrs(_updater({}, available=False), None) == {
        "connection_quality": 100,
        "latency_avg": 0.0,
        "status": "Excellent",
    }

    updater = _updater(
        {"RSSI": "-68"},
        available=True,
    )
    updater.connection_quality = {"success_rate": 90, "latency_avg": 0.25}

    assert sensors.get_connection_attrs(updater, None) == {
        "connection_quality": 90,
        "latency_avg": 0.0,
        "status": "Good",
        "wifi_rssi": -68,
    }


def test_time_drift_handles_invalid_timestamp_without_raising() -> None:
    assert sensors.get_time_drift(
        _updater({"systemTime": "bad", "timeZone": "3"}), None
    ) is None


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


def test_optimized_sensor_value_exceptions_are_logged_and_contained() -> None:
    updater = _updater({"value": "1"})
    spec = sensors.SensorSpec(
        key="broken_value",
        name="Broken Value",
        value_fn=lambda updater, hass: (_ for _ in ()).throw(RuntimeError("boom")),
        sensor_type=sensors.SensorType.DIAGNOSTIC,
    )
    sensor = spec.create_sensor(updater)

    assert sensor._update_native_value() is False
    assert sensor.native_value is None


def test_optimized_sensor_attribute_exceptions_clear_previous_attributes() -> None:
    updater = _updater({"value": "1"})
    spec = sensors.SensorSpec(
        key="broken_attrs",
        name="Broken Attributes",
        value_fn=lambda updater, hass: 1,
        sensor_type=sensors.SensorType.DIAGNOSTIC,
        attributes_fn=lambda updater, hass: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    sensor = spec.create_sensor(updater)
    sensor._attr_extra_state_attributes = {"old": "value"}

    assert sensor._update_extra_state_attributes() is True
    assert sensor.extra_state_attributes == {}


def test_monetary_cost_sensor_restores_last_reset_and_invalid_state() -> None:
    spec = sensors.SensorSpec(
        key="session_cost",
        name="Session Cost",
        value_fn=sensors.get_session_cost,
        sensor_type=sensors.SensorType.ENERGY,
        tracks_reset=True,
    )
    sensor = spec.create_sensor(_updater({"sessionMoney": "1.23"}))
    restored_at = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)
    state = SimpleNamespace(state="bad", attributes={"last_reset": restored_at})

    asyncio.run(sensor._async_restore_state(state))

    assert sensor.last_reset == restored_at
    assert sensor._prev_cost_value is None


def test_monetary_cost_sensor_ignores_unparseable_last_reset() -> None:
    spec = sensors.SensorSpec(
        key="session_cost",
        name="Session Cost",
        value_fn=sensors.get_session_cost,
        sensor_type=sensors.SensorType.ENERGY,
        tracks_reset=True,
    )
    sensor = spec.create_sensor(_updater({"sessionMoney": "1.23"}))
    state = SimpleNamespace(state="2.5", attributes={"last_reset": "not-a-date"})

    asyncio.run(sensor._async_restore_state(state))

    assert sensor.last_reset is None
    assert sensor._prev_cost_value == pytest.approx(2.5)


def test_icon_and_unit_display_constants_are_stable() -> None:
    """Guard the literal mdi:*/unit strings feeding SensorSpec.icon/unit.

    A typo here ships a broken icon or a wrong displayed unit to every user —
    these are user-visible, not cosmetic, even though nothing else in the
    module reads these constants back for its own logic.
    """
    assert sensors.ICON_FLASH == "mdi:flash"
    assert sensors.ICON_CURRENT_AC == "mdi:current-ac"
    assert sensors.ICON_CURRENCY_UAH == "mdi:currency-uah"
    assert sensors.UNIT_UAH_PER_KWH == "₴/kWh"
    assert sensors.UNIT_UAH == "UAH"


def test_error_log_rate_limit_capacity_matches_constant() -> None:
    """_MAX_ERROR_LOG_KEYS bounds the per-function rate-limit cache.

    Fill it to exactly the constant's value with distinct keys, then confirm
    the *next* new key evicts the oldest one instead of growing the cache
    further — this pins the exact capacity, not just "some" capacity.
    """
    log = sensors._SENSOR_FUNCTION_LOG
    log._last_logs.clear()
    try:
        for i in range(sensors._MAX_ERROR_LOG_KEYS):
            assert sensors._should_log_error(f"probe_{i}") is True
        assert len(log._last_logs) == 64

        # One more distinct key must evict, keeping the cache at capacity.
        assert sensors._should_log_error("probe_overflow") is True
        assert len(log._last_logs) == 64
        assert "probe_0" not in log._last_logs
    finally:
        log._last_logs.clear()


def test_voltage_current_power_sanity_ceilings_reject_over_limit_values() -> None:
    """Pin the exact upper sanity bounds for voltage/current/power getters."""
    from custom_components.eveus.const import MAX_POWER_W

    # Voltage: exactly at the ceiling passes (also exercises `>` not `>=`),
    # one unit above is rejected.
    assert sensors.get_voltage(_updater({"voltMeas1": "500"}), None) == 500
    assert sensors.get_voltage(_updater({"voltMeas1": "501"}), None) is None

    assert sensors.get_current(_updater({"curMeas1": "200"}), None) == 200
    assert sensors.get_current(_updater({"curMeas1": "201"}), None) is None

    assert sensors.get_power(_updater({"powerMeas": str(MAX_POWER_W)}), None) == MAX_POWER_W
    assert sensors.get_power(_updater({"powerMeas": str(MAX_POWER_W + 1)}), None) is None


def test_primary_rate_cost_rejects_above_rate_hundredths_ceiling() -> None:
    """_MAX_RATE_HUNDREDTHS bounds the raw tarif value before the /100 divide."""
    updater_ok = _updater({"activeTarif": "0", "tarif": "10000000"})
    updater_over = _updater({"activeTarif": "0", "tarif": "10000001"})

    assert sensors.get_primary_rate_cost(updater_ok, None) == pytest.approx(100000.0)
    assert sensors.get_primary_rate_cost(updater_over, None) is None


def test_active_rate_cost_maps_rate_2_to_tarif_b() -> None:
    updater = _updater({"activeTarif": "2", "tarifBValue": "300"})
    assert sensors.get_active_rate_cost(updater, None) == pytest.approx(3.0)


def test_sensor_spec_is_frozen() -> None:
    import dataclasses

    spec = sensors.SensorSpec(
        key="frozen_probe",
        name="Frozen Probe",
        value_fn=lambda updater, hass: 1,
        sensor_type=sensors.SensorType.DIAGNOSTIC,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.name = "Mutated"


def test_sensor_spec_precision_default_is_not_applied() -> None:
    """precision defaults to None so non-numeric sensors never get a bogus
    suggested_display_precision — only specs that pass precision explicitly
    should end up with one set."""
    spec = sensors.SensorSpec(
        key="precision_probe",
        name="Precision Probe",
        value_fn=lambda updater, hass: "text",
        sensor_type=sensors.SensorType.DIAGNOSTIC,
    )
    sensor = spec.create_sensor(_updater({}))
    assert sensor.suggested_display_precision is None


def test_sensor_spec_tracks_reset_defaults_to_plain_sensor() -> None:
    spec = sensors.SensorSpec(
        key="tracks_reset_probe",
        name="Tracks Reset Probe",
        value_fn=lambda updater, hass: 1,
        sensor_type=sensors.SensorType.DIAGNOSTIC,
    )
    sensor = spec.create_sensor(_updater({}))
    assert type(sensor) is sensors.OptimizedEveusSensor


def test_create_sensor_default_device_number_has_no_suffix() -> None:
    """create_sensor's device_number default (1) must not add a device
    suffix to the unique_id — a device-2 default would collide with real
    multi-device unique_ids the moment a caller omits the argument."""
    spec = sensors.SensorSpec(
        key="device_default_probe",
        name="Device Default Probe",
        value_fn=lambda updater, hass: 1,
        sensor_type=sensors.SensorType.DIAGNOSTIC,
    )
    sensor = spec.create_sensor(_updater({}))
    assert sensor.unique_id == "eveus_device_default_probe"


def test_make_value_getter_default_precision_rounds_to_int() -> None:
    """precision defaults to 0 in the factory itself."""
    getter = sensors._make_value_getter("probeKey")
    updater = _updater({"probeKey": "12.6"})
    assert getter(updater, None) == 13


def test_battery_voltage_rejects_reading_of_exactly_zero() -> None:
    """get_battery_voltage uses exclusive_min=True with minimum=0, so a
    reading of exactly 0 (implausible battery voltage) must be rejected, not
    just readings below 0."""
    updater = _updater({"vBat": "0"})
    assert sensors.get_battery_voltage(updater, None) is None


def test_get_data_value_short_circuits_on_either_offline_or_missing_data() -> None:
    """`not available or not data` must reject on EITHER condition, not only
    when both are true."""
    updater = _updater({}, available=True)  # available but data is empty
    assert sensors._get_data_value(updater, "missing_key", default="sentinel") is None


def test_voltage_and_power_getters_accept_reading_of_exactly_zero() -> None:
    """get_voltage/get_power both use minimum=0 (inclusive); a mutant raising
    minimum to 1 would reject a legitimate 0 reading."""
    assert sensors.get_voltage(_updater({"voltMeas1": "0"}), None) == 0
    assert sensors.get_power(_updater({"powerMeas": "0"}), None) == 0


def test_energy_and_cost_getters_precision_and_minimum_boundary() -> None:
    """Each of these getters is built with precision=2 and minimum=0 (inclusive).
    A reading of exactly 0 must pass (kills minimum->1 mutants and, since it
    can only come through if the factory reads the *correct* key, also kills
    key-name mutants); a value with a third decimal digit must round to 2
    places, not 3."""
    cases = [
        ("sessionEnergy", sensors.get_session_energy),
        ("totalEnergy", sensors.get_total_energy),
        ("IEM1", sensors.get_counter_a_energy),
        ("IEM2", sensors.get_counter_b_energy),
        ("IEM1_money", sensors.get_counter_a_cost),
        ("IEM2_money", sensors.get_counter_b_cost),
    ]
    for key, getter in cases:
        assert getter(_updater({key: "0"}), None) == 0
        assert getter(_updater({key: "12.345"}), None) == pytest.approx(12.35)


def test_rate_cost_getters_precision_and_minimum_boundary() -> None:
    """tarif/tarifAValue/tarifBValue getters divide by 100 before rounding to
    precision=2, and reject nothing at minimum=0 (raw 0 -> cost 0)."""
    cases = [
        ("tarif", sensors.get_primary_rate_cost),
        ("tarifAValue", sensors.get_rate2_cost),
        ("tarifBValue", sensors.get_rate3_cost),
    ]
    for key, getter in cases:
        assert getter(_updater({key: "0"}), None) == 0
        # 123.45 / 100 = 1.2345 -> rounds to 1.23 at precision 2, 1.234 at 3.
        assert getter(_updater({key: "123.45"}), None) == pytest.approx(1.23)


def test_box_and_plug_temperature_precision_is_whole_degrees() -> None:
    """Both temperature getters use precision=0; a mutant bumping precision to
    1 would keep the fractional digit instead of rounding to a whole degree."""
    assert sensors.get_box_temperature(_updater({"temperature1": "5.6"}), None) == 6
    assert sensors.get_plug_temperature(_updater({"temperature2": "5.6"}), None) == 6


def test_battery_voltage_precision_and_exclusive_minimum_boundary() -> None:
    """precision=2, and minimum=0 with exclusive_min=True: a tiny positive
    reading must still pass (kills a mutant that raises minimum to 1)."""
    assert sensors.get_battery_voltage(_updater({"vBat": "3.456"}), None) == pytest.approx(3.46)
    assert sensors.get_battery_voltage(_updater({"vBat": "0.5"}), None) == pytest.approx(0.5)


def test_leak_current_getters_key_precision_and_minimum_boundary() -> None:
    """get_leak_current/get_leak_current_peak: precision=0, minimum=0
    (inclusive). Reading 0 at the correct key must pass through as 0."""
    cases = [
        ("leakValue", sensors.get_leak_current),
        ("leakValueH", sensors.get_leak_current_peak),
    ]
    for key, getter in cases:
        assert getter is not None
        assert getter(_updater({key: "0"}), None) == 0
        assert getter(_updater({key: "5.6"}), None) == 6


def test_wifi_rssi_precision_minimum_and_maximum_boundaries() -> None:
    """get_wifi_rssi: precision=0, minimum=-120, maximum=0."""
    assert sensors.get_wifi_rssi(_updater({"RSSI": "-5.6"}), None) == -6
    assert sensors.get_wifi_rssi(_updater({"RSSI": "-121"}), None) is None
    assert sensors.get_wifi_rssi(_updater({"RSSI": "1"}), None) is None


def test_phase_getters_key_precision_and_minimum_boundary() -> None:
    """The four per-phase getters must each read their own dedicated key, be
    callable (not accidentally assigned None), keep their documented
    precision, and accept a reading of exactly 0 (minimum=0 inclusive)."""
    cases = [
        ("curMeas2", sensors.get_current_phase_2, 1),
        ("curMeas3", sensors.get_current_phase_3, 1),
        ("voltMeas2", sensors.get_voltage_phase_2, 0),
        ("voltMeas3", sensors.get_voltage_phase_3, 0),
    ]
    for key, getter, precision in cases:
        assert callable(getter)
        assert getter(_updater({key: "0"}), None) == 0
        if precision == 1:
            assert getter(_updater({key: "5.36"}), None) == pytest.approx(5.4)
        else:
            assert getter(_updater({key: "5.6"}), None) == 6


def test_charger_state_logs_warning_only_for_unmapped_states(caplog) -> None:
    """`state_value not in CHARGING_STATES` gates the once-per-value warning
    log; a mapped state must never trigger it, and an unmapped one must."""
    import logging

    mapped_state = next(iter(sensors.CHARGING_STATES))
    with caplog.at_level(logging.WARNING, logger="custom_components.eveus.sensor_definitions"):
        caplog.clear()
        sensors.get_charger_state(_updater({"state": str(mapped_state)}), None)
        assert not any("unrecognized device state" in r.message for r in caplog.records)

        caplog.clear()
        sensors.get_charger_state(_updater({"state": "9999"}), None)
        assert any("unrecognized device state" in r.message for r in caplog.records)


def test_session_time_getters_accept_zero_and_exact_max_boundary() -> None:
    """0 seconds is a valid (if odd) duration and must not be rejected; the
    upper bound uses `>` (exclusive), so exactly MAX_SESSION_TIME_SECONDS
    must still pass."""
    from custom_components.eveus.const import MAX_SESSION_TIME_SECONDS

    assert sensors.get_session_time(_updater({"sessionTime": "0"}), None) is not None
    assert sensors.get_session_time_attrs(_updater({"sessionTime": "0"}), None) == {
        "duration_seconds": 0
    }

    max_s = str(MAX_SESSION_TIME_SECONDS)
    assert sensors.get_session_time(_updater({"sessionTime": max_s}), None) is not None
    assert sensors.get_session_time_attrs(_updater({"sessionTime": max_s}), None) == {
        "duration_seconds": MAX_SESSION_TIME_SECONDS
    }


def test_active_rate_cost_accepts_zero_and_exact_ceiling_boundary() -> None:
    """get_active_rate_cost: `value < 0` (0 must pass) and `value >
    _MAX_RATE_HUNDREDTHS` (exactly the ceiling must still pass)."""
    from custom_components.eveus.sensor_definitions import _MAX_RATE_HUNDREDTHS

    assert sensors.get_active_rate_cost(
        _updater({"activeTarif": "0", "tarif": "0"}), None
    ) == 0
    assert (
        sensors.get_active_rate_cost(
            _updater({"activeTarif": "0", "tarif": str(_MAX_RATE_HUNDREDTHS)}), None
        )
        is not None
    )


def test_active_rate_cost_precision_rounds_to_two_places() -> None:
    """123.45 / 100 = 1.2345, which rounds to 1.23 at precision=2 (not
    1.234 at precision=3)."""
    updater = _updater({"activeTarif": "0", "tarif": "123.45"})
    assert sensors.get_active_rate_cost(updater, None) == pytest.approx(1.23)


def test_session_cost_precision_rounds_to_two_places() -> None:
    """get_session_cost is built with precision=2; a third decimal digit
    must round away, not survive at precision=3."""
    updater = _updater({"sessionMoney": "12.345"})
    assert sensors.get_session_cost(updater, None) == pytest.approx(12.35)


def test_format_minutes_boundary_values() -> None:
    """Valid range is [0, 1440): 0 is the earliest legal minute-of-day and
    must format, 1439 is the latest, and 1440 (a full day) is out of range
    and must be rejected, not clamped or accepted."""
    assert sensors._format_minutes(0) == "00:00"
    assert sensors._format_minutes(1439) == "23:59"
    assert sensors._format_minutes(1440) is None
    assert sensors._format_minutes(-1) is None
    assert sensors._format_minutes(None) is None


def test_schedule_attrs_window_requires_both_start_and_stop() -> None:
    """`if start and stop` must require BOTH to be present; a lone valid
    start (or stop) must not produce a half-open window attribute."""
    attrs_fn = sensors._make_schedule_attrs(1)
    only_start = attrs_fn(_updater({"sh1Start": "60"}), None)
    assert "window" not in only_start
    assert "start" not in only_start
    only_stop = attrs_fn(_updater({"sh1Stop": "120"}), None)
    assert "window" not in only_stop
    assert "stop" not in only_stop


def test_schedule_attrs_current_and_energy_zero_are_inclusive() -> None:
    """Both the current and energy caps use minimum=0 inclusive: a reading
    of exactly 0 must be kept, not treated as "unset"."""
    attrs_fn = sensors._make_schedule_attrs(1)
    current_zero = attrs_fn(
        _updater({"sh1CurrentEnable": "1", "sh1CurrentValue": "0"}), None
    )
    assert current_zero["current_limit_a"] == 0
    energy_zero = attrs_fn(
        _updater({"sh1EnergyEnable": "1", "sh1EnergyValue": "0"}), None
    )
    assert energy_zero["energy_limit_kwh"] == 0


def test_schedule_attrs_current_limit_boundary_at_max_current() -> None:
    """The current cap upper bound is inclusive: a reading exactly equal to
    max_current must be kept, not dropped as "above the model maximum"."""
    attrs_fn = sensors._make_schedule_attrs(1, max_current=16)
    at_max = attrs_fn(
        _updater({"sh1CurrentEnable": "1", "sh1CurrentValue": "16"}), None
    )
    assert at_max["current_limit_a"] == 16


def test_connection_quality_missing_success_rate_defaults_to_zero() -> None:
    """`metrics.get("success_rate", 0)` must default to 0 (unknown/no data
    reads as 0% quality), not silently default to something else."""
    updater = SimpleNamespace(available=True, data={}, connection_quality={})
    assert sensors.get_connection_quality(updater, None) == 0


@pytest.mark.parametrize(
    ("success_rate", "expected_status"),
    [
        (95, "Good"),
        (80, "Fair"),
        (60, "Poor"),
    ],
)
def test_connection_attrs_status_band_exact_boundaries(
    success_rate: int, expected_status: str
) -> None:
    """The status-band thresholds are strictly `>`, not `>=`: a reading
    exactly at a boundary belongs to the *lower* band."""
    updater = SimpleNamespace(
        available=True,
        data={},
        connection_quality={"success_rate": success_rate, "latency_avg": 0},
    )
    assert sensors.get_connection_attrs(updater, None)["status"] == expected_status


def test_connection_attrs_wifi_rssi_failure_excludes_field_entirely() -> None:
    """When get_wifi_rssi raises, the fallback must be `None` (field
    omitted), not a falsy-but-not-None sentinel like "" that would leak an
    empty wifi_rssi attribute into the entity."""
    updater = SimpleNamespace(
        available=True,
        data="not-a-dict",  # truthy but has no .get -> get_wifi_rssi raises
        connection_quality={"success_rate": 90, "latency_avg": 0.0},
    )
    attrs = sensors.get_connection_attrs(updater, None)
    assert "wifi_rssi" not in attrs


def test_current_set_and_adaptive_current_getters_precision_and_minimum() -> None:
    """Both current_set_getter and adaptive_current_getter are built with
    precision=0 and minimum=0 (inclusive): a fractional reading must round
    to a whole amp, and exactly 0 must pass through as 0, not be rejected."""
    specs = {s.name: s for s in sensors.create_sensor_specifications(max_current=16)}
    current_set_fn = specs["Current Set"].value_fn
    adaptive_fn = next(
        s.value_fn
        for s in sensors.create_sensor_specifications(max_current=16)
        if s.key == "adaptive_current_limit"
    )
    assert current_set_fn(_updater({"currentSet": "14.6"}), None) == 15
    assert current_set_fn(_updater({"currentSet": "0"}), None) == 0
    assert adaptive_fn(_updater({"aiModecurrent": "14.6"}), None) == 15
    assert adaptive_fn(_updater({"aiModecurrent": "0"}), None) == 0


# Each spec's icon string is a load-bearing value, not decoration: a typo'd or
# silently-altered `mdi:` name renders as a blank tile in the dashboard, and
# nothing else in the suite pins these strings. Pinning the full mapping also
# guards the "only ship verified MDI names" rule at the one place icons are set.
_EXPECTED_SPEC_ICONS = {
    "voltage": "mdi:flash",
    "current": "mdi:current-ac",
    "power": "mdi:flash",
    "current_set": "mdi:current-ac",
    "session_energy": "mdi:transmission-tower-export",
    "total_energy": "mdi:transmission-tower",
    "counter_a_energy": "mdi:counter",
    "counter_b_energy": "mdi:counter",
    "state": "mdi:state-machine",
    "substate": "mdi:information-variant",
    "not_charging_reason": "mdi:help-circle-outline",
    "ground": "mdi:electric-switch",
    "time_drift": "mdi:clock-alert-outline",
    "box_temperature": "mdi:thermometer",
    "plug_temperature": "mdi:thermometer-high",
    "battery_voltage": "mdi:battery",
    "leak_current": "mdi:current-dc",
    "leak_current_peak": "mdi:current-dc",
    "wifi_signal": "mdi:wifi",
    "session_time": "mdi:timer",
    "counter_a_cost": "mdi:currency-uah",
    "counter_b_cost": "mdi:currency-uah",
    "primary_rate_cost": "mdi:currency-uah",
    "active_rate_cost": "mdi:currency-uah",
    "rate_2_cost": "mdi:currency-uah",
    "rate_3_cost": "mdi:currency-uah",
    "rate_2_status": "mdi:clock-check",
    "rate_3_status": "mdi:clock-check",
    "session_cost": "mdi:cash",
    "adaptive_charging": "mdi:auto-mode",
    "adaptive_current_limit": "mdi:current-ac",
    "schedule_1": "mdi:calendar-clock",
    "schedule_2": "mdi:calendar-clock",
    "connection_quality": "mdi:connection",
}


@pytest.mark.parametrize(("key", "icon"), sorted(_EXPECTED_SPEC_ICONS.items()))
def test_spec_icon_names_are_pinned(key: str, icon: str) -> None:
    """Every icon-bearing spec keeps its exact MDI name."""
    specs = {s.key: s for s in sensors.get_sensor_specifications(phases=1)}
    assert specs[key].icon == icon


def test_spec_icon_inventory_is_complete() -> None:
    """The pinned mapping covers every icon-bearing spec, so a new sensor's
    icon cannot slip in unpinned."""
    actual = {
        s.key
        for s in sensors.get_sensor_specifications(phases=1)
        if getattr(s, "icon", None)
    }
    assert actual == set(_EXPECTED_SPEC_ICONS)


def test_get_current_accepts_zero_amps() -> None:
    """0 A is a real reading (car connected, not drawing), not an out-of-range
    one: the minimum is inclusive, so a parked-but-plugged charger reports 0
    rather than going unknown."""
    assert sensors.get_current(_updater({"curMeas1": "0"}), None) == 0
    assert sensors.get_current(_updater({"curMeas1": "6.4"}), None) == 6.4
    assert sensors.get_current(_updater({"curMeas1": "-1"}), None) is None


class TestChargerStateAttributes:
    """`raw_state` exposes the device code the visible state can't show —
    the firmware-1.x translated case and the unmapped-code case (issue #11).
    A plain mapped state must stay attribute-free."""

    def test_legacy_translated_state_exposes_original_code(self) -> None:
        attrs = sensors.get_charger_state_attributes(
            _updater({"state": 2, "_legacy_raw_state": 20}), None
        )
        assert attrs == {"raw_state": 20}

    def test_unmapped_state_code_is_exposed(self) -> None:
        attrs = sensors.get_charger_state_attributes(_updater({"state": 20}), None)
        assert attrs == {"raw_state": 20}

    def test_mapped_state_adds_no_attribute(self) -> None:
        assert sensors.get_charger_state_attributes(_updater({"state": 2}), None) == {}

    def test_missing_state_adds_no_attribute(self) -> None:
        assert sensors.get_charger_state_attributes(_updater({}), None) == {}

    def test_empty_payload_is_handled(self) -> None:
        """No payload yet (first poll pending) must not raise."""
        assert sensors.get_charger_state_attributes(_updater(None), None) == {}
