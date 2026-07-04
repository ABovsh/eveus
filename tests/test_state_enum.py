"""State/Substate sensors expose the ENUM device class with a full options list."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass

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
