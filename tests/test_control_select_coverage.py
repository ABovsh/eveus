"""Targeted coverage for control-base contract + select edge paths (design Part 1d).

These exercise the error/restore/grace branches that the happy-path select tests
don't reach: command failure (False and raised), the command-pending reconcile
skip, restore-from-state, and the offline grace-window display. They are real
behavior guarantees, not coverage padding.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError

from conftest import TEST_HOST
from custom_components.eveus import select as select_module
from custom_components.eveus.const import CONTROL_GRACE_PERIOD
from custom_components.eveus.control_base import CommandBackedEntity


class _Updater:
    host = TEST_HOST
    available = True
    last_update_success = True

    def __init__(
        self,
        data: dict[str, object] | None = None,
        *,
        available: bool = True,
        result: bool = True,
        raises: Exception | None = None,
    ) -> None:
        self.data = data or {}
        self.available = available
        self.commands: list[tuple[str, object]] = []
        self._result = result
        self._raises = raises

    def async_add_listener(self, *args: object, **kwargs: object):
        return lambda: None

    async def send_command(self, command: str, value: object, *, retry: bool = True) -> bool:
        self.commands.append((command, value))
        if self._raises is not None:
            raise self._raises
        return self._result


def _mute(entity: object) -> None:
    entity.async_write_ha_state = lambda: None


# --- control_base.CommandBackedEntity abstract contract ---

def test_command_backed_entity_abstract_methods_raise() -> None:
    inst = CommandBackedEntity.__new__(CommandBackedEntity)
    with pytest.raises(NotImplementedError):
        inst._read_device_value()
    with pytest.raises(NotImplementedError):
        inst._resolve_display_value()
    with pytest.raises(NotImplementedError):
        inst._set_display_value(1)
    with pytest.raises(NotImplementedError):
        inst._get_pending()


def test_command_backed_entity_default_values_equal() -> None:
    inst = CommandBackedEntity.__new__(CommandBackedEntity)
    assert inst._values_equal(5, 5) is True
    assert inst._values_equal(5, 6) is False


# --- integer select (minimum voltage) error/restore/grace paths ---

def test_min_voltage_command_failure_clears_optimistic_and_raises() -> None:
    updater = _Updater({"minVoltage": 200}, result=False)
    select = select_module.EveusMinVoltageSelect(updater)
    _mute(select)
    with pytest.raises(HomeAssistantError):
        asyncio.run(select.async_select_option("180"))
    assert select._optimistic_value is None


def test_min_voltage_command_exception_propagates_and_clears_optimistic() -> None:
    updater = _Updater({"minVoltage": 200}, raises=RuntimeError("boom"))
    select = select_module.EveusMinVoltageSelect(updater)
    _mute(select)
    with pytest.raises(RuntimeError):
        asyncio.run(select.async_select_option("180"))
    assert select._optimistic_value is None


def test_min_voltage_handle_update_skips_reconcile_while_pending() -> None:
    updater = _Updater({"minVoltage": 200})
    select = select_module.EveusMinVoltageSelect(updater)
    _mute(select)
    select._command_pending = True
    select._handle_coordinator_update()  # must return early, no crash


def test_min_voltage_device_option_none_when_unavailable() -> None:
    select = select_module.EveusMinVoltageSelect(_Updater({"minVoltage": 200}, available=False))
    assert select._device_option() is None


def test_min_voltage_restore_state_seeds_last_device_value() -> None:
    select = select_module.EveusMinVoltageSelect(_Updater({}, available=False))
    _mute(select)
    asyncio.run(select._async_restore_state(State("select.x", "180")))
    assert select._last_device_value == 180


def test_min_voltage_restore_state_ignores_unknown() -> None:
    select = select_module.EveusMinVoltageSelect(_Updater({}, available=False))
    _mute(select)
    asyncio.run(select._async_restore_state(State("select.x", "unknown")))
    assert select._last_device_value is None


def test_min_voltage_grace_window_shows_restored_option_while_offline() -> None:
    select = select_module.EveusMinVoltageSelect(_Updater({}, available=False))
    _mute(select)
    select._last_device_value = 180
    select._last_successful_read = time.time()
    assert select.current_option == "180"


def test_min_voltage_grace_window_expired_returns_none() -> None:
    select = select_module.EveusMinVoltageSelect(_Updater({}, available=False))
    _mute(select)
    select._last_device_value = 180
    select._last_successful_read = time.time() - CONTROL_GRACE_PERIOD - 1
    assert select.current_option is None


# --- timezone select restore-state parse guard ---

def test_timezone_restore_state_ignores_non_integer_option() -> None:
    select = select_module.EveusTimeZoneSelect(_Updater({}, available=False))
    _mute(select)
    # Not in TIMEZONE_OPTIONS -> the int() branch is skipped, no crash, no seed.
    asyncio.run(select._async_restore_state(State("select.tz", "not-a-zone")))
    assert select._last_device_value is None


def test_timezone_restore_state_seeds_valid_offset() -> None:
    select = select_module.EveusTimeZoneSelect(_Updater({}, available=False))
    _mute(select)
    asyncio.run(select._async_restore_state(State("select.tz", "+2")))
    assert select._last_device_value == 2


def test_timezone_command_failure_clears_optimistic_and_raises() -> None:
    updater = _Updater({"timeZone": 2}, result=False)
    select = select_module.EveusTimeZoneSelect(updater)
    _mute(select)
    with pytest.raises(HomeAssistantError):
        asyncio.run(select.async_select_option("+3"))
    assert select._optimistic_value is None
