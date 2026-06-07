"""Tests for the Ground Protection switch."""
from __future__ import annotations

import asyncio

from homeassistant.helpers.entity import EntityCategory

from conftest import EveusTestUpdater as _Updater
from conftest import disable_state_writes as _disable_state_writes
from custom_components.eveus.switch import BaseSwitchEntity, SWITCH_DESCRIPTIONS


def _description():
    return next(
        description
        for description in SWITCH_DESCRIPTIONS
        if description.key == "ground_protection"
    )


def _switch(updater: _Updater) -> BaseSwitchEntity:
    return BaseSwitchEntity(updater, _description())


def test_ground_protection_switch_contract() -> None:
    description = _description()

    assert description.name == "Ground Protection"
    assert description.command == "groundCtrl"
    assert description.state_key == "groundCtrl"
    assert description.entity_category is EntityCategory.CONFIG
    assert description.entity_registry_enabled_default is False


def test_ground_protection_switch_reads_device_state() -> None:
    disabled = _switch(_Updater({"groundCtrl": 0}))
    enabled = _switch(_Updater({"groundCtrl": 1}))

    assert disabled._resolve_state() is False
    assert enabled._resolve_state() is True


def test_ground_protection_switch_toggles_both_directions() -> None:
    updater = _Updater({"groundCtrl": 0})
    entity = _switch(updater)
    _disable_state_writes(entity)

    asyncio.run(entity.async_turn_on())
    assert updater.commands == [("groundCtrl", 1)]
    assert updater.command_extras == [None]
    assert entity.is_on is True

    asyncio.run(entity.async_turn_off())
    assert updater.commands == [("groundCtrl", 1), ("groundCtrl", 0)]
    assert updater.command_extras == [None, None]
    assert entity.is_on is False
