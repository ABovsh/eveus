"""Device triggers exposed in the automation UI."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.eveus import device_trigger
from custom_components.eveus.const import (
    DOMAIN,
    EVENT_CHARGING_FINISHED,
    EVENT_CHARGING_STARTED,
)


EXPECTED_TYPES = {
    "charging_started",
    "charging_finished",
    "error_occurred",
    "car_connected",
    "car_disconnected",
}


@pytest.mark.asyncio
async def test_get_triggers_lists_all_types() -> None:
    triggers = await device_trigger.async_get_triggers(Mock(), "dev-1")
    assert {t["type"] for t in triggers} == EXPECTED_TYPES
    assert all(t["domain"] == DOMAIN and t["device_id"] == "dev-1" for t in triggers)


@pytest.mark.asyncio
async def test_attach_builds_event_trigger_scoped_to_device_number() -> None:
    entry = SimpleNamespace(
        domain=DOMAIN, runtime_data=SimpleNamespace(device_number=3)
    )
    device = SimpleNamespace(config_entries={"eid-1"})
    hass = Mock()
    hass.config_entries.async_get_entry = Mock(return_value=entry)
    registry = Mock()
    registry.async_get = Mock(return_value=device)
    config = {
        "platform": "device",
        "domain": DOMAIN,
        "device_id": "dev-1",
        "type": "charging_finished",
    }
    with (
        patch.object(device_trigger.dr, "async_get", return_value=registry),
        # cv.template inside the real schema needs a hass context var that only
        # exists in a running HA instance; the schema itself is HA-owned code.
        patch.object(device_trigger.event_trigger, "TRIGGER_SCHEMA", new=lambda c: c),
        patch.object(
            device_trigger.event_trigger, "async_attach_trigger", new=AsyncMock()
        ) as attach,
    ):
        await device_trigger.async_attach_trigger(hass, config, Mock(), Mock())
    event_config = attach.call_args.args[1]
    assert event_config["event_type"] == EVENT_CHARGING_FINISHED
    assert event_config["event_data"] == {"device_number": 3}


@pytest.mark.asyncio
async def test_attach_defaults_to_device_number_1_when_unset() -> None:
    entry = SimpleNamespace(
        domain=DOMAIN, runtime_data=SimpleNamespace(device_number=None), data={}
    )
    device = SimpleNamespace(config_entries={"eid-1"})
    hass = Mock()
    hass.config_entries.async_get_entry = Mock(return_value=entry)
    registry = Mock()
    registry.async_get = Mock(return_value=device)
    config = {
        "platform": "device",
        "domain": DOMAIN,
        "device_id": "dev-1",
        "type": "charging_started",
    }
    with (
        patch.object(device_trigger.dr, "async_get", return_value=registry),
        # cv.template inside the real schema needs a hass context var that only
        # exists in a running HA instance; the schema itself is HA-owned code.
        patch.object(device_trigger.event_trigger, "TRIGGER_SCHEMA", new=lambda c: c),
        patch.object(
            device_trigger.event_trigger, "async_attach_trigger", new=AsyncMock()
        ) as attach,
    ):
        await device_trigger.async_attach_trigger(hass, config, Mock(), Mock())
    event_config = attach.call_args.args[1]
    assert event_config["event_type"] == EVENT_CHARGING_STARTED
    assert event_config["event_data"] == {"device_number": 1}
