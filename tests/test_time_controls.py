"""Tests for the Sync Time button and Time Zone select."""
from __future__ import annotations

import asyncio
import time

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.eveus.button import EveusSyncTimeButton
from custom_components.eveus.select import (
    TIMEZONE_OPTIONS,
    EveusTimeZoneSelect,
)


class _Updater:
    host = "192.168.1.50"
    available = True
    last_update_success = True

    def __init__(self, data: dict[str, object] | None = None) -> None:
        self.data = data or {}
        self.commands: list[tuple[str, object]] = []
        self.command_result = True

    def async_add_listener(self, *args: object, **kwargs: object):
        return lambda: None

    async def send_command(self, command: str, value: object, *, retry: bool = True) -> bool:
        self.commands.append((command, value))
        self.last_retry = retry
        return self.command_result


def _disable_state_writes(entity: object) -> None:
    entity.async_write_ha_state = lambda: None


def test_timezone_options_cover_full_range() -> None:
    """Select must offer the full -12..+14 hour range the firmware accepts."""
    assert TIMEZONE_OPTIONS[0] == "-12"
    assert TIMEZONE_OPTIONS[-1] == "+14"
    assert "0" in TIMEZONE_OPTIONS
    assert len(TIMEZONE_OPTIONS) == 27


def test_sync_time_button_sends_current_utc() -> None:
    """Sync Time sends `systemTime=<current UTC seconds>`.

    Firmware stores UTC and returns it as local-as-unix in /main, so the
    correct value to push is plain `int(time.time())` — no tz offset math.
    """
    updater = _Updater({"timeZone": 3, "systemTime": 1778988900})
    button = EveusSyncTimeButton(updater)
    _disable_state_writes(button)

    before = int(time.time())
    asyncio.run(button.async_press())
    after = int(time.time())

    assert len(updater.commands) == 1
    cmd, value = updater.commands[0]
    assert cmd == "systemTime"
    assert isinstance(value, int)
    assert before <= value <= after


def test_sync_time_button_raises_on_failure() -> None:
    """Failed sync surfaces as a HA toast."""
    updater = _Updater({"timeZone": 3})
    updater.command_result = False
    button = EveusSyncTimeButton(updater)
    _disable_state_writes(button)

    with pytest.raises(HomeAssistantError):
        asyncio.run(button.async_press())


def test_timezone_select_reflects_coordinator_value() -> None:
    """Current option mirrors the latest `timeZone` value from /main."""
    updater = _Updater({"timeZone": 3})
    select = EveusTimeZoneSelect(updater)
    _disable_state_writes(select)
    assert select.current_option == "+3"

    updater.data["timeZone"] = -5
    assert select.current_option == "-5"


def test_timezone_select_handles_missing_value() -> None:
    """No timeZone field → current_option is None, not a crash."""
    updater = _Updater({})
    select = EveusTimeZoneSelect(updater)
    _disable_state_writes(select)
    assert select.current_option is None


def test_timezone_select_handles_string_value() -> None:
    """Firmware may return timeZone as a string; coerce safely."""
    updater = _Updater({"timeZone": "3"})
    select = EveusTimeZoneSelect(updater)
    _disable_state_writes(select)
    assert select.current_option == "+3"


def test_timezone_select_sends_integer_command() -> None:
    """Selecting an option sends `timeZone=<int>` via the command manager."""
    updater = _Updater({"timeZone": 0})
    select = EveusTimeZoneSelect(updater)
    _disable_state_writes(select)

    asyncio.run(select.async_select_option("+5"))
    assert updater.commands == [("timeZone", 5)]

    asyncio.run(select.async_select_option("-3"))
    assert updater.commands[-1] == ("timeZone", -3)

    asyncio.run(select.async_select_option("0"))
    assert updater.commands[-1] == ("timeZone", 0)


def test_timezone_select_rejects_unknown_option() -> None:
    """Out-of-range options are rejected without hitting the charger."""
    updater = _Updater({"timeZone": 0})
    select = EveusTimeZoneSelect(updater)
    _disable_state_writes(select)

    with pytest.raises(HomeAssistantError):
        asyncio.run(select.async_select_option("+99"))
    assert updater.commands == []


def test_timezone_select_raises_on_failure() -> None:
    """Failed command surfaces as a HA toast."""
    updater = _Updater({"timeZone": 0})
    updater.command_result = False
    select = EveusTimeZoneSelect(updater)
    _disable_state_writes(select)

    with pytest.raises(HomeAssistantError):
        asyncio.run(select.async_select_option("+3"))


def test_timezone_select_holds_optimistic_value_until_device_confirms() -> None:
    """After a successful write, current_option reflects the picked value even
    if the next poll still returns the old timeZone — prevents the UI snap-back
    that other writable controls already protect against via optimistic state.
    """
    updater = _Updater({"timeZone": 0})
    select = EveusTimeZoneSelect(updater)
    _disable_state_writes(select)

    asyncio.run(select.async_select_option("+3"))
    assert select.current_option == "+3"

    # Coordinator hasn't observed the new value yet — UI must still show +3.
    select._handle_coordinator_update()
    assert select.current_option == "+3"

    # Device finally confirms +3 → optimistic state clears, device value used.
    updater.data["timeZone"] = 3
    select._handle_coordinator_update()
    assert select.current_option == "+3"
    assert select._optimistic_value is None


def test_timezone_select_optimistic_cleared_on_failed_write() -> None:
    """A rejected command must not leave a stale optimistic value behind."""
    updater = _Updater({"timeZone": 0})
    updater.command_result = False
    select = EveusTimeZoneSelect(updater)
    _disable_state_writes(select)

    with pytest.raises(HomeAssistantError):
        asyncio.run(select.async_select_option("+7"))

    assert select._optimistic_value is None
    assert select.current_option == "0"


def test_timezone_select_optimistic_clears_on_device_mismatch() -> None:
    """If the device persistently reports a different value past the TTL, the
    optimistic state expires and the UI follows the device — same contract as
    OptimisticControlMixin uses for switches and numbers.
    """
    updater = _Updater({"timeZone": 0})
    select = EveusTimeZoneSelect(updater)
    _disable_state_writes(select)

    asyncio.run(select.async_select_option("+5"))
    assert select.current_option == "+5"

    # Force the optimistic value far enough into the past to expire.
    select._optimistic_value_time = time.time() - 3600
    updater.data["timeZone"] = 2
    select._handle_coordinator_update()
    assert select._optimistic_value is None
    assert select.current_option == "+2"
