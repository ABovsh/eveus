"""P4.1 — one optimistic-write lifecycle for switches and selects.

`BaseSwitchEntity` and both selects used to re-implement
`control_base.CommandBackedEntity._send_pinned_command` (pin display, send,
release pin, re-resolve on any exit). This pins them onto the shared
lifecycle so a future fix to that sequence cannot be made in one copy and
missed in the others.
"""
from __future__ import annotations

import asyncio

import pytest
from homeassistant.exceptions import HomeAssistantError

from conftest import EveusTestUpdater as _Updater
from conftest import disable_state_writes as _disable_state_writes
from custom_components.eveus.select import EveusTimeZoneSelect, _EveusIntegerSelect
from custom_components.eveus.switch import BaseSwitchEntity, SWITCH_DESCRIPTIONS


def test_time_zone_select_is_an_integer_select() -> None:
    """EveusTimeZoneSelect is the twin _EveusIntegerSelect was extracted from."""
    assert issubclass(EveusTimeZoneSelect, _EveusIntegerSelect)


def test_base_switch_entity_has_no_private_command_lifecycle() -> None:
    """The switch's own re-implementation must be gone, not just unused."""
    assert "_async_send_command" not in BaseSwitchEntity.__dict__
    assert "_async_send_command_or_raise" not in BaseSwitchEntity.__dict__


def test_switch_turn_on_routes_through_the_shared_pinned_command(monkeypatch) -> None:
    updater = _Updater({"evseEnabled": 0})
    switch = BaseSwitchEntity(updater, SWITCH_DESCRIPTIONS[0], 1)
    _disable_state_writes(switch)

    calls: list[dict] = []

    async def fake_pinned(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(switch, "_send_pinned_command", fake_pinned)
    asyncio.run(switch.async_turn_on())

    assert len(calls) == 1
    assert calls[0]["device_value"] == 1
    assert calls[0]["accepted"] is True


def test_switch_turn_off_command_rejection_raises(monkeypatch) -> None:
    """The shared lifecycle's rejection path still surfaces as HomeAssistantError."""
    updater = _Updater({"evseEnabled": 1})
    updater.command_result = False
    switch = BaseSwitchEntity(updater, SWITCH_DESCRIPTIONS[0], 1)
    _disable_state_writes(switch)

    with pytest.raises(HomeAssistantError):
        asyncio.run(switch.async_turn_off())


def test_switch_command_extra_still_rides_along(monkeypatch) -> None:
    """OCPP's sibling `ocppVendor` field must still travel with the toggle."""
    updater = _Updater({"ocppEnabled": 0})
    description = next(d for d in SWITCH_DESCRIPTIONS if d.key == "ocpp")
    switch = BaseSwitchEntity(updater, description, 1)
    _disable_state_writes(switch)

    asyncio.run(switch.async_turn_on())

    assert updater.command_extras == [{"ocppVendor": 1}]
