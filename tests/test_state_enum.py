"""State/Substate sensors expose the ENUM device class with a full options list."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

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


# Every sensor whose getter maps a payload int through a closed dict: the
# option list must be exactly that dict's values, so the automation UI offers
# a dropdown instead of free text (the reason state/substate became ENUM).
_CLOSED_SET_SENSORS = {
    "ground": ("Connected", "Not Connected"),
    "rate_2_status": ("Enabled", "Disabled"),
    "rate_3_status": ("Enabled", "Disabled"),
    "adaptive_charging": ("Off", "Voltage", "Auto", "Power"),
    "schedule_1": ("Enabled", "Disabled"),
    "schedule_2": ("Enabled", "Disabled"),
}


@pytest.mark.parametrize(("key", "values"), sorted(_CLOSED_SET_SENSORS.items()))
def test_closed_set_sensors_are_enums(key: str, values: tuple) -> None:
    spec = _spec(key)
    assert spec.device_class == SensorDeviceClass.ENUM
    assert set(spec.options) == set(values)


@pytest.mark.parametrize("key", sorted(_CLOSED_SET_SENSORS))
def test_closed_set_getters_never_leave_their_option_list(key: str) -> None:
    """An ENUM value outside the options list is dropped by HA — the sensor
    would silently read `unknown` instead of its real state."""
    spec = _spec(key)
    updater = SimpleNamespace(data={}, available=True, connection_quality={})
    produced = set()
    for raw in list(range(-1, 12)) + ["1", "0", None, "junk"]:
        updater.data = {k: raw for k in _PAYLOAD_KEYS[key]}
        produced.add(spec.value_fn(updater, None))
    produced.discard(None)
    # Without this the test passes vacuously: a wrong _PAYLOAD_KEYS entry makes
    # the getter read a key that is never present, so it returns None for every
    # input and the empty set trivially satisfies the subset check below.
    assert produced, f"{key}: the sweep produced no values — _PAYLOAD_KEYS is wrong"
    assert produced <= set(spec.options)


_PAYLOAD_KEYS = {
    "ground": ("ground",),
    "rate_2_status": ("tarifAEnable",),
    "rate_3_status": ("tarifBEnable",),
    "adaptive_charging": ("aiStatus",),
    "schedule_1": ("sh1Enabled",),
    "schedule_2": ("sh2Enabled",),
}
