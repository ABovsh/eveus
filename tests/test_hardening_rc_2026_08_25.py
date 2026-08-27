"""Hardening tests for the rc round of 2026-08-25."""
from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass

from custom_components.eveus.number import (
    CHARGING_CURRENT_DESCRIPTION,
    GLOBAL_LIMIT_NUMBERS,
    SCHEDULE_LIMIT_NUMBERS,
    UNDERVOLTAGE_THRESHOLD_NUMBER,
)

# Units Home Assistant has a number device class for. UAH and % are absent
# from NumberDeviceClass, so those setpoints legitimately declare none.
_EXPECTED_DEVICE_CLASS = {
    "A": NumberDeviceClass.CURRENT,
    "V": NumberDeviceClass.VOLTAGE,
    "kWh": NumberDeviceClass.ENERGY,
    "min": NumberDeviceClass.DURATION,
}


def test_every_physically_typed_setpoint_declares_its_device_class() -> None:
    """A unit without its device class is a control HA cannot label or group.

    Pinned as an invariant over all setpoints rather than per entity: the
    schedule current limits were the one A-valued control left undeclared
    while Charging Current, in the same unit, carried the class.
    """
    descriptions = (
        CHARGING_CURRENT_DESCRIPTION,
        UNDERVOLTAGE_THRESHOLD_NUMBER,
        *GLOBAL_LIMIT_NUMBERS,
        *SCHEDULE_LIMIT_NUMBERS,
    )

    mismatched = {
        description.key: (
            description.native_unit_of_measurement,
            description.device_class,
        )
        for description in descriptions
        if description.native_unit_of_measurement in _EXPECTED_DEVICE_CLASS
        and description.device_class
        != _EXPECTED_DEVICE_CLASS[description.native_unit_of_measurement]
    }

    assert mismatched == {}
