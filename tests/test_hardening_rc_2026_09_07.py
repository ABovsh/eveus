"""Regression coverage for device registry compatibility."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from conftest import EveusTestUpdater
from custom_components.eveus.common_base import BaseEveusEntity
from custom_components.eveus.session_history import (
    LastSessionCostSensor,
    LastSessionDurationSensor,
    LastSessionEnergySensor,
)


@pytest.mark.parametrize("attached", [True, False])
def test_metadata_uses_attached_device_without_legacy_lookup(monkeypatch, attached):
    """Metadata belongs to the entity's device, never a global identifier match."""
    class Probe(BaseEveusEntity):
        ENTITY_NAME = "Registry Probe"

    updater = EveusTestUpdater({"verFWMain": "R3.05.2"})
    entity = Probe(updater)
    entity.hass = SimpleNamespace()
    entity.device_entry = SimpleNamespace(id="owned-device") if attached else None
    registry = Mock()
    registry.async_get_device.side_effect = AssertionError("deprecated global lookup")
    monkeypatch.setattr("custom_components.eveus.common_base.dr.async_get", lambda hass: registry)

    entity._maybe_finalize_device_info()

    registry.async_get_device.assert_not_called()
    if attached:
        assert registry.async_update_device.call_args.args == ("owned-device",)
        assert registry.async_update_device.call_args.kwargs["sw_version"] == "R3.05.2"
        assert updater._device_registry_finalized is True
    else:
        registry.async_update_device.assert_not_called()
        assert not getattr(updater, "_device_registry_finalized", False)


@pytest.mark.parametrize("sensor_class", [LastSessionEnergySensor, LastSessionCostSensor, LastSessionDurationSensor])
@pytest.mark.parametrize("value", [10**400, -(10**400)], ids=["huge-positive", "huge-negative"])
def test_public_session_event_rejects_oversized_integer(sensor_class, value):
    """The event listener must reject oversized external values without raising."""
    sensor = sensor_class(EveusTestUpdater({}))
    sensor._handle_finished_event(SimpleNamespace(data={
        "device_number": 1,
        sensor._event_field: value,
        "reason": "complete",
    }))
    assert sensor.native_value is None
