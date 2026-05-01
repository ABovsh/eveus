"""Unit tests for optimized sensor entity behavior."""
from __future__ import annotations

from types import SimpleNamespace

from homeassistant.helpers.entity import EntityCategory

from custom_components.eveus.sensor_definitions import (
    OptimizedEveusSensor,
    SensorSpec,
    SensorType,
    get_connection_attrs,
    get_connection_quality,
    get_ground_status,
    get_session_time,
    get_session_time_attrs,
    get_system_time,
)


class _Updater:
    host = "192.168.1.50"
    available = True
    last_update_success = True
    data = {"value": "10"}
    connection_quality = {
        "success_rate": 75,
        "latency_avg": 0.42,
    }

    def async_add_listener(self, *args: object, **kwargs: object):
        return lambda: None


def _sensor(value_fn, *, sensor_type: SensorType = SensorType.MEASUREMENT):
    spec = SensorSpec(
        key="test_sensor",
        name="Test Sensor",
        value_fn=value_fn,
        sensor_type=sensor_type,
        icon="mdi:test-tube",
        device_class="power",
        state_class="measurement",
        unit="W",
        precision=1,
        category=EntityCategory.DIAGNOSTIC,
        attributes_fn=lambda updater, hass: {"ok": True},
    )
    entity = OptimizedEveusSensor(_Updater(), spec)
    entity.hass = SimpleNamespace(config=SimpleNamespace(time_zone="Europe/Kiev"))
    return entity


def test_optimized_sensor_applies_description_fields() -> None:
    entity = _sensor(lambda updater, hass: 10)

    assert entity.icon == "mdi:test-tube"
    assert entity.device_class == "power"
    assert entity.state_class == "measurement"
    assert entity.native_unit_of_measurement == "W"
    assert entity.suggested_display_precision == 1
    assert entity.entity_category == EntityCategory.DIAGNOSTIC
    assert entity.extra_state_attributes == {"ok": True}


def test_optimized_sensor_uses_fresh_coordinator_data() -> None:
    calls = 0

    def value_fn(updater, hass):
        nonlocal calls
        calls += 1
        return calls

    entity = _sensor(value_fn)

    assert entity.native_value == 1
    assert entity.native_value == 2
    assert calls == 2


def test_optimized_sensor_recalculates_calculated_values() -> None:
    calls = 0

    def value_fn(updater, hass):
        nonlocal calls
        calls += 1
        return calls

    entity = _sensor(value_fn, sensor_type=SensorType.CALCULATED)

    assert entity.native_value == 1
    assert entity.native_value == 2


def test_optimized_sensor_returns_none_when_offline() -> None:
    updater = _Updater()
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="value",
            name="Value",
            value_fn=lambda updater, hass: float(updater.data["value"]),
            sensor_type=SensorType.MEASUREMENT,
        ),
    )
    entity.hass = object()

    assert entity.native_value == 10
    updater.available = False
    assert entity.native_value is None


def test_optimized_sensor_error_paths_are_rate_limited() -> None:
    entity = _sensor(lambda updater, hass: (_ for _ in ()).throw(ValueError("boom")))

    assert entity.native_value is None
    assert entity.native_value is None


def test_session_ground_system_time_and_connection_helpers() -> None:
    hass = SimpleNamespace(config=SimpleNamespace(time_zone="Europe/Kiev"))
    updater = SimpleNamespace(
        available=True,
        data={"sessionTime": "3660", "ground": "0", "systemTime": "1714300000"},
        connection_quality={"success_rate": 75, "latency_avg": 0.42},
    )

    assert get_session_time(updater, hass) == "1h 01m"
    assert get_session_time_attrs(updater, hass) == {"duration_seconds": 3660}
    assert get_ground_status(updater, hass) == "Not Connected"
    assert get_system_time(updater, hass)
    assert get_connection_quality(updater, hass) == 75
    assert get_connection_attrs(updater, hass)["status"] == "Fair"


def test_connection_helpers_handle_errors() -> None:
    updater = SimpleNamespace(available=True)

    assert get_connection_quality(updater, None) == 100
    assert get_connection_attrs(updater, None) == {"status": "Error"}
