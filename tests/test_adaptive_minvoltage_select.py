"""Tests for Adaptive Mode and Minimum voltage select entities."""
from __future__ import annotations

import asyncio

import pytest
from homeassistant.exceptions import HomeAssistantError

from conftest import TEST_HOST
from custom_components.eveus import select as select_module


class _Updater:
    host = TEST_HOST
    available = True
    last_update_success = True

    def __init__(self, data: dict[str, object] | None = None) -> None:
        self.data = data or {}
        self.commands: list[tuple[str, object]] = []

    def async_add_listener(self, *args: object, **kwargs: object):
        return lambda: None

    async def send_command(
        self, command: str, value: object, *, retry: bool = True
    ) -> bool:
        self.commands.append((command, value))
        return True


def _disable_state_writes(entity: object) -> None:
    entity.async_write_ha_state = lambda: None


@pytest.mark.parametrize(
    ("device_value", "option"),
    [(0, "Off"), (1, "Voltage"), (2, "Auto"), (3, "Power"), (99, None)],
)
def test_adaptive_mode_maps_device_values(
    device_value: int, option: str | None
) -> None:
    select = select_module.EveusAdaptiveModeSelect(_Updater({"aiStatus": device_value}))
    _disable_state_writes(select)

    assert select.current_option == option


def test_adaptive_mode_writes_canonical_ai_mode_key() -> None:
    updater = _Updater({"aiStatus": 1})
    select = select_module.EveusAdaptiveModeSelect(updater)
    _disable_state_writes(select)

    asyncio.run(select.async_select_option("Power"))

    assert updater.commands == [("aiMode", 3)]


def test_adaptive_mode_optimistic_value_survives_stale_poll() -> None:
    updater = _Updater({"aiStatus": 1})
    select = select_module.EveusAdaptiveModeSelect(updater)
    _disable_state_writes(select)

    asyncio.run(select.async_select_option("Power"))
    select._handle_coordinator_update()

    assert select.current_option == "Power"


def test_adaptive_mode_rejects_unsupported_option() -> None:
    updater = _Updater({"aiStatus": 1})
    select = select_module.EveusAdaptiveModeSelect(updater)
    _disable_state_writes(select)

    with pytest.raises(HomeAssistantError):
        asyncio.run(select.async_select_option("Active"))

    assert updater.commands == []


def test_minimum_voltage_has_fixed_options() -> None:
    select = select_module.EveusMinVoltageSelect(_Updater({"minVoltage": 200}))

    assert select.options == ["200", "180", "175", "170", "165", "160", "155", "150"]


def test_minimum_voltage_writes_integer_value() -> None:
    updater = _Updater({"minVoltage": 200})
    select = select_module.EveusMinVoltageSelect(updater)
    _disable_state_writes(select)

    asyncio.run(select.async_select_option("180"))

    assert updater.commands == [("minVoltage", 180)]


def test_minimum_voltage_off_list_device_value_is_unknown() -> None:
    select = select_module.EveusMinVoltageSelect(_Updater({"minVoltage": 190}))

    assert select.current_option is None
