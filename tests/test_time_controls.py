"""Tests for the Sync Time button and Time Zone select."""
from __future__ import annotations

import asyncio
import datetime as dt
import time
from types import SimpleNamespace

import pytest
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError

from conftest import TEST_HOST
from custom_components.eveus.button import EveusSyncTimeButton
from custom_components.eveus.common_base import BaseEveusEntity
from custom_components.eveus.select import (
    TIMEZONE_OPTIONS,
    EveusTimeZoneSelect,
)
from custom_components.eveus.time import (
    TIME_DESCRIPTIONS,
    EveusScheduleTimeEntity,
    async_setup_entry,
    minutes_to_time,
    time_to_minutes,
)


class _Updater:
    host = TEST_HOST
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


def test_sync_time_button_sends_current_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sync Time sends `systemTime=<current UTC seconds>`.

    Firmware stores UTC and returns it as local-as-unix in /main, so the
    correct value to push is plain `int(time.time())` — no tz offset math.
    """
    updater = _Updater({"timeZone": 3, "systemTime": 1778988900})
    button = EveusSyncTimeButton(updater)
    _disable_state_writes(button)
    monkeypatch.setattr("custom_components.eveus.button.time.time", lambda: 1778988912.9)

    asyncio.run(button.async_press())

    assert updater.commands == [("systemTime", 1778988912)]


def test_sync_time_button_raises_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failed sync surfaces as a HA toast."""
    updater = _Updater({"timeZone": 3})
    updater.command_result = False
    button = EveusSyncTimeButton(updater)
    _disable_state_writes(button)
    monkeypatch.setattr("custom_components.eveus.button.time.time", lambda: 1778988912.9)

    with pytest.raises(HomeAssistantError, match="did not accept"):
        asyncio.run(button.async_press())
    assert updater.commands == [("systemTime", 1778988912)]


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

    with pytest.raises(HomeAssistantError, match="did not accept"):
        asyncio.run(select.async_select_option("+3"))
    assert updater.commands == [("timeZone", 3)]


def test_timezone_select_rolls_back_when_command_raises() -> None:
    """Transport exceptions clear optimistic UI instead of leaving a stale value."""

    class RaisingUpdater(_Updater):
        async def send_command(self, command: str, value: object, *, retry: bool = True) -> bool:
            self.commands.append((command, value))
            raise RuntimeError("transport failed")

    updater = RaisingUpdater({"timeZone": 0})
    select = EveusTimeZoneSelect(updater)
    _disable_state_writes(select)

    with pytest.raises(RuntimeError, match="transport failed"):
        asyncio.run(select.async_select_option("+3"))

    assert updater.commands == [("timeZone", 3)]
    assert select._optimistic_value is None
    assert select.current_option == "0"


def test_timezone_select_update_handles_missing_device_option() -> None:
    updater = _Updater({})
    select = EveusTimeZoneSelect(updater)
    _disable_state_writes(select)

    select._handle_coordinator_update()

    assert select.current_option is None


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


def _schedule_entity(
    data: dict[str, object] | None = None,
    *,
    available: bool = True,
) -> EveusScheduleTimeEntity:
    updater = _Updater(data)
    updater.available = available
    entity = EveusScheduleTimeEntity(updater, TIME_DESCRIPTIONS[0])
    _disable_state_writes(entity)
    return entity


def test_minutes_to_time_rejects_invalid_values() -> None:
    assert minutes_to_time(None) is None
    assert minutes_to_time("bad") is None
    assert minutes_to_time(-1) is None
    assert minutes_to_time(1440) is None
    assert minutes_to_time(1439) == dt.time(23, 59)


def test_time_to_minutes_discards_seconds() -> None:
    assert time_to_minutes(dt.time(1, 2, 59)) == 62


def test_schedule_time_resolves_valid_device_minutes() -> None:
    entity = _schedule_entity({"sh1Start": "75"})
    assert entity._resolve_minutes() == 75


def test_schedule_time_falls_back_to_recent_restored_value() -> None:
    entity = _schedule_entity({"sh1Start": "bad"})
    entity._last_device_value = 90
    entity._last_successful_read = time.time()

    assert entity._resolve_minutes() == 90


def test_schedule_time_returns_none_for_expired_restore() -> None:
    entity = _schedule_entity({"sh1Start": "bad"})
    entity._last_device_value = 90
    entity._last_successful_read = time.time() - 3600

    assert entity._resolve_minutes() is None


def test_schedule_time_uses_recent_device_value_when_payload_missing() -> None:
    """A brief missing/invalid payload should not blank a recently read value."""
    entity = _schedule_entity({})
    entity._last_device_value = 345
    entity._last_successful_read = time.time()

    assert entity._resolve_minutes() == 345


def test_schedule_time_ignores_recent_device_value_after_grace_period() -> None:
    """Stale restored/device values expire instead of lingering indefinitely."""
    entity = _schedule_entity({})
    entity._last_device_value = 345
    entity._last_successful_read = time.time() - 3600

    assert entity._resolve_minutes() is None


def test_schedule_time_set_value_sends_minutes_and_keeps_optimistic_value() -> None:
    entity = _schedule_entity({"sh1Start": 60})

    asyncio.run(entity.async_set_value(dt.time(6, 30, 45)))

    assert entity._updater.commands == [("sh1Start", 390)]
    assert entity.native_value == dt.time(6, 30)
    assert entity._optimistic_value == 390
    assert entity._pending_value is None


def test_schedule_time_set_value_raises_on_rejected_command() -> None:
    entity = _schedule_entity({"sh1Start": 60})
    entity._updater.command_result = False

    with pytest.raises(HomeAssistantError, match="did not accept"):
        asyncio.run(entity.async_set_value(dt.time(7, 15)))

    assert entity._updater.commands == [("sh1Start", 435)]
    assert entity._pending_value is None
    assert entity.native_value == dt.time(1, 0)


def test_schedule_time_restore_accepts_clock_state() -> None:
    entity = _schedule_entity({})

    asyncio.run(entity._async_restore_state(State("time.test", "08:45:00")))

    assert entity._last_device_value == 525
    assert entity.native_value == dt.time(8, 45)


def test_schedule_time_restore_accepts_hour_minute_state() -> None:
    entity = _schedule_entity({})

    asyncio.run(entity._async_restore_state(State("time.test", "07:30")))

    assert entity.native_value == dt.time(7, 30)


def test_schedule_time_restore_garbage_state_leaves_native_value_none() -> None:
    entity = _schedule_entity({})

    asyncio.run(entity._async_restore_state(State("time.test", "not-a-time")))

    assert entity.native_value is None


@pytest.mark.parametrize("state", [None, "unknown", "unavailable", "not-a-time"])
def test_schedule_time_restore_ignores_invalid_state(state: str | None) -> None:
    entity = _schedule_entity({})
    restored = None if state is None else State("time.test", state)

    asyncio.run(entity._async_restore_state(restored))

    assert entity._last_device_value is None
    assert entity.native_value is None


def test_schedule_time_update_returns_early_while_write_pending() -> None:
    entity = _schedule_entity({"sh1Start": 120})
    entity._pending_value = 300
    entity._attr_native_value = dt.time(5, 0)

    entity._handle_coordinator_update()

    assert entity.native_value == dt.time(5, 0)


def test_schedule_time_update_tracks_confirmed_device_value() -> None:
    entity = _schedule_entity({"sh1Start": 120})

    entity._handle_coordinator_update()

    assert entity.native_value == dt.time(2, 0)
    assert entity._last_device_value == 120


def test_schedule_time_update_uses_recent_last_device_value_for_invalid_payload() -> None:
    entity = _schedule_entity({"sh1Start": "bad"})
    entity._last_device_value = 240
    entity._last_successful_read = time.time()

    entity._handle_coordinator_update()

    assert entity.native_value == dt.time(4, 0)


def test_schedule_time_update_expires_stale_optimistic_mismatch() -> None:
    entity = _schedule_entity({"sh1Start": 120})
    entity._optimistic_value = 390
    entity._optimistic_value_time = time.time() - 3600

    entity._handle_coordinator_update()

    assert entity._optimistic_value is None
    assert entity.native_value == dt.time(2, 0)


def test_schedule_time_update_keeps_fresh_optimistic_until_device_confirms() -> None:
    entity = _schedule_entity({"sh1Start": 120})
    entity._optimistic_value = 390
    entity._optimistic_value_time = time.time()

    entity._handle_coordinator_update()

    assert entity._optimistic_value == 390
    assert entity.native_value == dt.time(6, 30)


def test_schedule_time_added_to_hass_sets_initial_native_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def noop_added_to_hass(self) -> None:
        return None

    monkeypatch.setattr(BaseEveusEntity, "async_added_to_hass", noop_added_to_hass)
    entity = _schedule_entity({"sh1Start": 615})

    asyncio.run(entity.async_added_to_hass())

    assert entity.native_value == dt.time(10, 15)


def test_time_setup_entry_adds_four_schedule_entities() -> None:
    added: list[EveusScheduleTimeEntity] = []
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(
            updater=_Updater({"sh1Start": 60}),
            device_number=2,
        )
    )

    asyncio.run(async_setup_entry(object(), entry, lambda entities: added.extend(entities)))

    assert [entity.entity_description.key for entity in added] == [
        "schedule_1_start",
        "schedule_1_stop",
        "schedule_2_start",
        "schedule_2_stop",
    ]
    assert {entity._device_number for entity in added} == {2}
