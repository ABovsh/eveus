"""Unit tests for schedule switch + time entities (sh1/sh2)."""
from __future__ import annotations

import asyncio
import datetime as dt

from conftest import TEST_HOST
from custom_components.eveus.switch import (
    BaseSwitchEntity,
    SWITCH_DESCRIPTIONS,
)
from custom_components.eveus.time import (
    EveusScheduleTimeEntity,
    TIME_DESCRIPTIONS,
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

    def async_add_listener(self, *args, **kwargs):
        return lambda: None

    async def send_command(
        self, command, value, *, retry: bool = True, extra=None
    ) -> bool:
        self.commands.append((command, value))
        self.last_extra = extra
        return self.command_result


def _silence(entity) -> None:
    entity.async_write_ha_state = lambda: None


def _by_key(key: str):
    for desc in SWITCH_DESCRIPTIONS:
        if desc.key == key:
            return desc
    raise AssertionError(f"No switch description for {key}")


def _time_by_key(key: str):
    for desc in TIME_DESCRIPTIONS:
        if desc.key == key:
            return desc
    raise AssertionError(f"No time description for {key}")


# ─── minutes <-> time helpers ────────────────────────────────────────────────

def test_minutes_time_round_trip_endpoints() -> None:
    assert minutes_to_time(0) == dt.time(0, 0)
    assert minutes_to_time(1439) == dt.time(23, 59)
    assert minutes_to_time(1380) == dt.time(23, 0)
    assert minutes_to_time(420) == dt.time(7, 0)

    assert time_to_minutes(dt.time(0, 0)) == 0
    assert time_to_minutes(dt.time(23, 59)) == 1439
    assert time_to_minutes(dt.time(23, 0)) == 1380


def test_minutes_to_time_rejects_out_of_range_and_garbage() -> None:
    assert minutes_to_time(None) is None
    assert minutes_to_time(-1) is None
    assert minutes_to_time(1440) is None
    assert minutes_to_time("abc") is None


# ─── schedule enable switches ────────────────────────────────────────────────

def test_schedule_enable_switches_are_registered() -> None:
    keys = {d.key for d in SWITCH_DESCRIPTIONS}
    assert "schedule_1_enabled" in keys
    assert "schedule_2_enabled" in keys

    sh1 = _by_key("schedule_1_enabled")
    assert sh1.command == "sh1Enabled"
    assert sh1.state_key == "sh1Enabled"

    sh2 = _by_key("schedule_2_enabled")
    assert sh2.command == "sh2Enabled"
    assert sh2.state_key == "sh2Enabled"


def test_schedule_switch_toggle_sends_correct_command() -> None:
    updater = _Updater({"sh1Enabled": 0})
    entity = BaseSwitchEntity(updater, _by_key("schedule_1_enabled"))
    _silence(entity)

    asyncio.run(entity.async_turn_on())
    assert updater.commands[-1] == ("sh1Enabled", 1)
    assert entity.is_on is True

    asyncio.run(entity.async_turn_off())
    assert updater.commands[-1] == ("sh1Enabled", 0)
    assert entity.is_on is False


# ─── schedule time entities ──────────────────────────────────────────────────

def test_time_entity_reads_minutes_from_payload() -> None:
    updater = _Updater({"sh1Start": 1380, "sh1Stop": 420})
    start = EveusScheduleTimeEntity(updater, _time_by_key("schedule_1_start"))
    stop = EveusScheduleTimeEntity(updater, _time_by_key("schedule_1_stop"))

    assert minutes_to_time(start._resolve_minutes()) == dt.time(23, 0)
    assert minutes_to_time(stop._resolve_minutes()) == dt.time(7, 0)


def test_time_entity_set_value_posts_int_minutes() -> None:
    updater = _Updater({"sh1Start": 1380})
    entity = EveusScheduleTimeEntity(updater, _time_by_key("schedule_1_start"))
    _silence(entity)

    asyncio.run(entity.async_set_value(dt.time(22, 30)))
    assert updater.commands[-1] == ("sh1Start", 22 * 60 + 30)
    assert entity.native_value == dt.time(22, 30)


def test_time_entity_set_value_at_midnight_sends_zero() -> None:
    updater = _Updater({"sh2Stop": 0})
    entity = EveusScheduleTimeEntity(updater, _time_by_key("schedule_2_stop"))
    _silence(entity)

    asyncio.run(entity.async_set_value(dt.time(0, 0)))
    assert updater.commands[-1] == ("sh2Stop", 0)


def test_time_entity_all_four_keys_registered() -> None:
    keys = {d.key for d in TIME_DESCRIPTIONS}
    assert keys == {
        "schedule_1_start",
        "schedule_1_stop",
        "schedule_2_start",
        "schedule_2_stop",
    }
    for desc in TIME_DESCRIPTIONS:
        assert desc.command == desc.state_key
        assert desc.command.startswith("sh") and desc.command.endswith(("Start", "Stop"))
