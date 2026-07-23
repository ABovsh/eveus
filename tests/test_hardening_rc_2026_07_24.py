"""Hardening round 2026-07-24: device_trigger OverflowError parity, Last
Session Cost device_class."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from homeassistant.components.sensor import SensorDeviceClass

from custom_components.eveus import device_trigger
from custom_components.eveus.const import DOMAIN
from custom_components.eveus.session_history import LastSessionCostSensor


def test_device_number_for_survives_non_finite_stored_value() -> None:
    """A non-finite device_number in entry.data must not crash trigger resolution.

    int() raises OverflowError on non-finite floats (e.g. a corrupted/hand-edited
    Store entry holding Infinity). __init__.py's identical coercion of the same
    field already guards OverflowError; this fallback must match it instead of
    crashing async_attach_trigger for every trigger card on the device.
    """
    entry = SimpleNamespace(
        domain=DOMAIN,
        runtime_data=None,
        data={"device_number": float("inf")},
    )
    device = SimpleNamespace(config_entries={"eid-1"})
    hass = Mock()
    hass.config_entries.async_get_entry = Mock(return_value=entry)
    registry = Mock()
    registry.async_get = Mock(return_value=device)

    with patch.object(device_trigger.dr, "async_get", return_value=registry):
        result = device_trigger._device_number_for(hass, "dev-1")

    assert result == 1


def test_last_session_cost_sensor_has_monetary_device_class() -> None:
    """Every cost sensor in the integration declares MONETARY except this one.

    Without it the frontend skips currency formatting/semantics for a sensor
    that already carries a currency unit (UAH) and icon.
    """
    instance = object.__new__(LastSessionCostSensor)
    assert instance.device_class == SensorDeviceClass.MONETARY
