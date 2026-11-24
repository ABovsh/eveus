"""Tests for Adaptive Mode and Minimum voltage select entities."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.restore_state import RestoredExtraData

from conftest import snapshot_of
from conftest import OutageClock
from conftest import TEST_HOST
from custom_components.eveus import select as select_module


class _Updater(OutageClock):
    host = TEST_HOST
    last_update_success = True

    def __init__(self, data: dict[str, object] | None = None) -> None:
        self.data = data or {}
        self.commands: list[tuple[str, object]] = []

    @property
    def snapshot(self):
        # Derived on read: these tests drive the control by assigning `data`.
        return snapshot_of(self)

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


def test_minimum_voltage_writes_integer_value() -> None:
    updater = _Updater({"minVoltage": 200})
    select = select_module.EveusMinVoltageSelect(updater)
    _disable_state_writes(select)

    asyncio.run(select.async_select_option("180"))

    assert updater.commands == [("minVoltage", 180)]


def test_minimum_voltage_off_list_device_value_is_unknown() -> None:
    select = select_module.EveusMinVoltageSelect(_Updater({"minVoltage": 190}))

    assert select.current_option is None


async def test_1x_minimum_voltage_remembers_only_accepted_commands():
    updater = _Updater()
    updater.is_legacy_firmware = True
    select = select_module.EveusMinVoltageSelect(updater)
    select.async_write_ha_state = Mock()
    select.async_get_last_extra_data = AsyncMock(return_value=None)
    await select._async_restore_state(State("select.minimum_voltage", "200"))

    assert select.available and select.assumed_state
    assert select.current_option is None
    assert select.extra_restore_state_data is None

    async def accept(command, value):
        assert select.current_option is None
        assert select.extra_restore_state_data is None
        return True

    updater.send_command = AsyncMock(side_effect=accept)
    await select.async_select_option("175")
    updater.send_command.assert_awaited_once_with("minVoltage", 175)
    assert select.current_option == "175"
    assert select.extra_restore_state_data.as_dict() == {"last_legacy_command": 175}
    select.async_write_ha_state.assert_called_once()

    select.async_write_ha_state.reset_mock()
    select._optimistic_value_time -= 1000
    select._handle_coordinator_update()
    updater.send_command = AsyncMock(return_value=False)
    with pytest.raises(HomeAssistantError):
        await select.async_select_option("180")
    assert select.current_option == "175"
    select.async_write_ha_state.assert_not_called()

    restored = select_module.EveusMinVoltageSelect(updater)
    restored.async_get_last_extra_data = AsyncMock(return_value=select.extra_restore_state_data)
    updater.is_legacy_firmware = False
    await restored._async_restore_state(State("select.minimum_voltage", "unavailable"))
    assert restored.current_option is None
    updater.is_legacy_firmware = True
    assert restored.current_option == "175"

    # Report the same option and publish the change in assumed_state.
    updater.data = {"minVoltage": 175}
    select._handle_coordinator_update()
    assert not select.assumed_state and select.current_option == "175"
    select.async_write_ha_state.assert_called_once()

    updater.data = {"minVoltage": 180}
    select._handle_coordinator_update()
    assert select.current_option == "180"


@pytest.mark.parametrize("family", ["classic", "reve"])
async def test_minimum_voltage_non_1x_preserves_readback_and_restore(family):
    updater = _Updater()
    updater.firmware_family = family
    updater.is_legacy_firmware = False
    select = select_module.EveusMinVoltageSelect(updater)
    select.async_get_last_extra_data = AsyncMock(
        return_value=RestoredExtraData({"last_legacy_command": 150})
    )
    await select._async_restore_state(State("select.minimum_voltage", "200"))
    assert not select.assumed_state and select.current_option == "200"
    assert select.extra_restore_state_data is None
    updater.data = {"minVoltage": 180}
    assert select.current_option == "180"


async def test_minimum_voltage_non_1x_shows_an_accepted_choice_before_readback():
    updater = _Updater({"minVoltage": 180})
    updater.is_legacy_firmware = False
    select = select_module.EveusMinVoltageSelect(updater)
    select.async_write_ha_state = Mock()

    await select.async_select_option("175")

    assert updater.commands == [("minVoltage", 175)]
    assert select.current_option == "175"
    select.async_write_ha_state.assert_called()


async def test_1x_minimum_voltage_restores_only_an_integer_command():
    updater = _Updater()
    updater.is_legacy_firmware = True
    select = select_module.EveusMinVoltageSelect(updater)
    select.async_get_last_extra_data = AsyncMock(
        return_value=RestoredExtraData({"last_legacy_command": 175.0})
    )
    await select._async_restore_state(State("select.minimum_voltage", "unknown"))
    assert select.current_option is None


def test_minimum_voltage_non_1x_publishes_each_readback_change():
    updater = _Updater({"minVoltage": 180})
    updater.is_legacy_firmware = False
    select = select_module.EveusMinVoltageSelect(updater)
    select.async_write_ha_state = Mock()
    select._handle_coordinator_update()
    updater.data = {"minVoltage": 175}
    select._handle_coordinator_update()
    assert select.async_write_ha_state.call_count == 2


def test_legacy_adaptive_mode_offers_and_round_trips_auto_2() -> None:
    updater = _Updater({"aiStatus": 2})
    updater.is_legacy_firmware = False
    entity = select_module.EveusAdaptiveModeSelect(updater)
    entity.async_write_ha_state = Mock()
    entity._handle_coordinator_update()
    entity.async_write_ha_state.reset_mock()

    updater.is_legacy_firmware = True
    entity._handle_coordinator_update()
    entity._handle_coordinator_update()
    assert entity.options == ["Off", "Voltage", "Auto", "Power", "Auto 2"]
    entity.async_write_ha_state.assert_called_once_with()

    updater.data["aiStatus"] = 4
    assert entity.current_option == "Auto 2"
    asyncio.run(entity.async_select_option("Auto 2"))
    assert updater.commands == [("aiMode", 4)]


@pytest.mark.parametrize("legacy", [None, False, 1])
def test_adaptive_mode_rejects_auto_2_without_confirmed_legacy(legacy) -> None:
    updater = _Updater({"aiStatus": 4})
    if legacy is not None:
        updater.is_legacy_firmware = legacy
    entity = select_module.EveusAdaptiveModeSelect(updater)
    _disable_state_writes(entity)

    assert entity.options == ["Off", "Voltage", "Auto", "Power"]
    assert entity.current_option is None
    with pytest.raises(HomeAssistantError, match="Unsupported Adaptive Mode: Auto 2"):
        asyncio.run(entity.async_select_option("Auto 2"))
    assert updater.commands == []
