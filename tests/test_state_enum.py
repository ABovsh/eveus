"""State/Substate sensors expose the ENUM device class with a full options list."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.helpers.entity import EntityCategory

from custom_components.eveus.const import (
    CHARGING_STATES,
    ERROR_STATES,
    NORMAL_SUBSTATES,
)
from custom_components.eveus.sensor_definitions import create_sensor_specifications


def _spec(key: str):
    specs = [s for s in create_sensor_specifications() if s.key == key]
    assert len(specs) == 1
    return specs[0]


def test_state_sensor_is_enum_with_all_states() -> None:
    spec = _spec("state")
    assert spec.device_class == SensorDeviceClass.ENUM
    assert set(CHARGING_STATES.values()) <= set(spec.options)
    # get_charging_state returns "Unknown" for out-of-domain values.
    assert "Unknown" in spec.options


def test_substate_sensor_is_enum_with_all_substates_and_errors() -> None:
    spec = _spec("substate")
    assert spec.device_class == SensorDeviceClass.ENUM
    options = set(spec.options)
    assert set(NORMAL_SUBSTATES.values()) <= options
    # Error-state labels, minus "No Error" which the getter never returns.
    assert (set(ERROR_STATES.values()) - {"No Error"}) <= options
    assert {"Unknown State", "Unknown Error"} <= options
    assert "No Error" not in options


def test_enum_specs_carry_no_unit_or_state_class() -> None:
    for key in ("state", "substate"):
        spec = _spec(key)
        assert spec.unit is None
        assert spec.state_class is None


def test_sensor_instance_gets_options_attr() -> None:
    from unittest.mock import MagicMock

    updater = MagicMock()
    updater.device_number = 1
    sensor = _spec("state").create_sensor(updater)
    assert sensor._attr_device_class == SensorDeviceClass.ENUM
    assert set(CHARGING_STATES.values()) <= set(sensor._attr_options)


def test_not_charging_reason_spec_is_fully_wired() -> None:
    """Pin every attribute the sensor's usefulness depends on.

    Dropping any of these fails silently in production: without the ENUM class
    and options the automation UI falls back to free text, and without the
    attributes function the `error`/`suspend_errors` attributes simply stop
    appearing — no exception, no log line.
    """
    from custom_components.eveus.sensor_definitions import (
        NOT_CHARGING_REASON_OPTIONS,
        get_not_charging_reason,
        get_not_charging_reason_attrs,
    )

    spec = _spec("not_charging_reason")
    assert spec.device_class == SensorDeviceClass.ENUM
    assert spec.options == NOT_CHARGING_REASON_OPTIONS
    assert spec.value_fn is get_not_charging_reason
    assert spec.attributes_fn is get_not_charging_reason_attrs
    assert spec.category == EntityCategory.DIAGNOSTIC
    assert spec.unit is None
    assert spec.state_class is None
