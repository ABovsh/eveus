"""Opt-in: keep the charger's Time Zone on Home Assistant's current UTC offset.

The charger holds one whole-hour offset and never follows daylight saving
(issue #21): the REVE app and the Time Zone select are the only things that
move it, so the charger runs an hour off after every clock change.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta, timezone
from types import SimpleNamespace

import pytest
from homeassistant.util import dt as dt_util

from custom_components.eveus import config_flow
from custom_components.eveus.const import (
    CONF_FOLLOW_HA_TIME_ZONE,
    CONF_SOC_MODE,
    SOC_MODE_ADVANCED,
    SOC_MODE_BASIC,
    TIME_ZONE_FOLLOW_POLLS,
    TIME_ZONE_FOLLOW_RETRY_POLLS,
)
from custom_components.eveus.snapshot import EveusSnapshot
from custom_components.eveus.time_zone_follow import (
    TimeZoneFollower,
    update_time_zone_follow,
)

HOUR = 3600


def _set_ha_offset(seconds: int) -> None:
    dt_util.set_default_time_zone(timezone(timedelta(seconds=seconds)))


@pytest.fixture(autouse=True)
def _ha_in_kyiv_winter():
    """HA local time UTC+2 (Kyiv winter) unless a test says otherwise."""
    original = dt_util.DEFAULT_TIME_ZONE
    _set_ha_offset(2 * HOUR)
    yield
    dt_util.set_default_time_zone(original)


def _poll(charger_zone: int | None) -> EveusSnapshot:
    data = {} if charger_zone is None else {"timeZone": charger_zone}
    return EveusSnapshot.parse(data, None)


# --- the decision ------------------------------------------------------------


def test_charger_on_the_ha_offset_is_left_alone() -> None:
    follower = TimeZoneFollower()

    for _ in range(TIME_ZONE_FOLLOW_POLLS * 2):
        assert follower.evaluate(_poll(2), 2 * HOUR) is None


def test_a_wrong_offset_is_written_only_after_several_polls_agree() -> None:
    follower = TimeZoneFollower()

    for _ in range(TIME_ZONE_FOLLOW_POLLS - 1):
        assert follower.evaluate(_poll(3), 2 * HOUR) is None

    assert follower.evaluate(_poll(3), 2 * HOUR) == 2


def test_a_poll_that_agrees_restarts_the_count() -> None:
    follower = TimeZoneFollower()
    for _ in range(TIME_ZONE_FOLLOW_POLLS - 1):
        follower.evaluate(_poll(3), 2 * HOUR)

    assert follower.evaluate(_poll(2), 2 * HOUR) is None

    for _ in range(TIME_ZONE_FOLLOW_POLLS - 1):
        assert follower.evaluate(_poll(3), 2 * HOUR) is None
    assert follower.evaluate(_poll(3), 2 * HOUR) == 2


def test_a_poll_without_the_offset_neither_counts_nor_restarts() -> None:
    follower = TimeZoneFollower()
    for _ in range(TIME_ZONE_FOLLOW_POLLS - 1):
        follower.evaluate(_poll(3), 2 * HOUR)

    assert follower.evaluate(_poll(None), 2 * HOUR) is None

    assert follower.evaluate(_poll(3), 2 * HOUR) == 2


@pytest.mark.parametrize("local_seconds", [5 * HOUR + 1800, 5 * HOUR + 2700, -(3 * HOUR + 1800)])
def test_a_fractional_local_offset_is_never_written(local_seconds: int) -> None:
    """The select holds whole hours: any value written would still be wrong."""
    follower = TimeZoneFollower()

    for _ in range(TIME_ZONE_FOLLOW_POLLS * 3):
        assert follower.evaluate(_poll(3), local_seconds) is None


def test_a_negative_whole_hour_offset_is_written_as_is() -> None:
    follower = TimeZoneFollower()

    results = [follower.evaluate(_poll(-4), -5 * HOUR) for _ in range(TIME_ZONE_FOLLOW_POLLS)]

    assert results[-1] == -5


def test_a_write_that_did_not_take_is_retried_only_after_a_pause() -> None:
    follower = TimeZoneFollower()
    for _ in range(TIME_ZONE_FOLLOW_POLLS):
        written = follower.evaluate(_poll(3), 2 * HOUR)
    assert written == 2

    for _ in range(TIME_ZONE_FOLLOW_RETRY_POLLS):
        assert follower.evaluate(_poll(3), 2 * HOUR) is None

    assert follower.evaluate(_poll(3), 2 * HOUR) == 2


def test_the_pause_ends_when_the_charger_agrees() -> None:
    follower = TimeZoneFollower()
    for _ in range(TIME_ZONE_FOLLOW_POLLS):
        follower.evaluate(_poll(3), 2 * HOUR)
    follower.evaluate(_poll(2), 2 * HOUR)

    results = [follower.evaluate(_poll(3), 2 * HOUR) for _ in range(TIME_ZONE_FOLLOW_POLLS)]

    assert results[-1] == 2


# --- the poll listener -------------------------------------------------------


class _Entry:
    """Entry double that runs background tasks the way HA's entry does."""

    def __init__(self) -> None:
        self.tasks: list = []

    def async_create_background_task(self, hass, target, name, eager_start=True):
        self.tasks.append(target)
        return None


class _Updater:
    def __init__(self, charger_zone: int | None, *, available: bool = True, send=None) -> None:
        self.available = available
        self.last_update_success = available
        self.snapshot = _poll(charger_zone)
        self.sent: list[tuple[str, int]] = []
        self._send = send

    async def send_command(self, command, value, **_):
        self.sent.append((command, value))
        if self._send is not None:
            return await self._send()
        return True


def _drive(updater: _Updater, follower: TimeZoneFollower, entry: _Entry, polls: int) -> None:
    for _ in range(polls):
        update_time_zone_follow(None, entry, updater, follower)
    for coro in entry.tasks:
        asyncio.run(coro)
    entry.tasks.clear()


def test_the_listener_sends_the_ha_offset_through_the_charger_command() -> None:
    updater, entry = _Updater(3), _Entry()

    _drive(updater, TimeZoneFollower(), entry, TIME_ZONE_FOLLOW_POLLS)

    assert updater.sent == [("timeZone", 2)]


def test_the_listener_stays_quiet_when_the_charger_is_in_sync() -> None:
    updater, entry = _Updater(2), _Entry()

    _drive(updater, TimeZoneFollower(), entry, TIME_ZONE_FOLLOW_POLLS * 2)

    assert updater.sent == []
    assert entry.tasks == []


def test_the_listener_ignores_polls_while_the_charger_is_unreachable() -> None:
    updater, entry = _Updater(3, available=False), _Entry()

    _drive(updater, TimeZoneFollower(), entry, TIME_ZONE_FOLLOW_POLLS * 3)

    assert updater.sent == []


def test_polls_during_a_write_do_not_start_a_second_one() -> None:
    updater, entry, follower = _Updater(3), _Entry(), TimeZoneFollower()

    for _ in range(TIME_ZONE_FOLLOW_POLLS):
        update_time_zone_follow(None, entry, updater, follower)
    assert len(entry.tasks) == 1
    # The write has not finished; the pause alone would not cover a slow one.
    for _ in range(TIME_ZONE_FOLLOW_RETRY_POLLS + TIME_ZONE_FOLLOW_POLLS + 1):
        update_time_zone_follow(None, entry, updater, follower)

    assert len(entry.tasks) == 1


def test_a_refused_write_does_not_raise_and_frees_the_follower() -> None:
    async def refused() -> bool:
        return False

    updater, entry, follower = _Updater(3, send=refused), _Entry(), TimeZoneFollower()

    _drive(updater, follower, entry, TIME_ZONE_FOLLOW_POLLS)

    assert follower.sending is False


def test_a_write_that_raises_frees_the_follower() -> None:
    async def broken() -> bool:
        raise RuntimeError("charger unreachable")

    updater, entry, follower = _Updater(3, send=broken), _Entry(), TimeZoneFollower()

    _drive(updater, follower, entry, TIME_ZONE_FOLLOW_POLLS)

    assert follower.sending is False


# --- the options form --------------------------------------------------------


def _options_flow(data: dict) -> config_flow.EveusOptionsFlow:
    entry = SimpleNamespace(data=data, unique_id="host", entry_id="e1")

    class _ConfigEntries:
        def async_update_entry(self, e, *, data):
            e.data = data

        async def async_reload(self, _entry_id):
            return True

    flow = config_flow.EveusOptionsFlow(entry)
    flow.hass = SimpleNamespace(
        config_entries=_ConfigEntries(), states=SimpleNamespace(get=lambda _entity_id: None)
    )
    flow.async_show_form = lambda *, step_id, data_schema=None: {
        "type": "form",
        "step_id": step_id,
        "data_schema": data_schema,
    }
    flow.async_create_entry = lambda *, title, data: {"type": "create_entry", "data": data}
    return flow


def _field(schema, name):
    return next(key for key in schema.schema if key.schema == name)


def test_the_options_form_offers_the_switch_off_by_default() -> None:
    flow = _options_flow({CONF_SOC_MODE: SOC_MODE_BASIC})

    form = asyncio.run(flow.async_step_init())

    assert _field(form["data_schema"], CONF_FOLLOW_HA_TIME_ZONE).default() is False


def test_the_options_form_shows_the_stored_choice() -> None:
    flow = _options_flow({CONF_SOC_MODE: SOC_MODE_BASIC, CONF_FOLLOW_HA_TIME_ZONE: True})

    form = asyncio.run(flow.async_step_init())

    assert _field(form["data_schema"], CONF_FOLLOW_HA_TIME_ZONE).default() is True


def test_turning_it_on_is_stored_in_the_entry() -> None:
    flow = _options_flow({CONF_SOC_MODE: SOC_MODE_BASIC})

    asyncio.run(
        flow.async_step_init({CONF_SOC_MODE: SOC_MODE_BASIC, CONF_FOLLOW_HA_TIME_ZONE: True})
    )

    assert flow._entry.data[CONF_FOLLOW_HA_TIME_ZONE] is True


def test_turning_it_off_removes_the_key() -> None:
    flow = _options_flow({CONF_SOC_MODE: SOC_MODE_BASIC, CONF_FOLLOW_HA_TIME_ZONE: True})

    asyncio.run(
        flow.async_step_init({CONF_SOC_MODE: SOC_MODE_BASIC, CONF_FOLLOW_HA_TIME_ZONE: False})
    )

    assert CONF_FOLLOW_HA_TIME_ZONE not in flow._entry.data


def test_a_submit_without_the_field_keeps_the_stored_choice() -> None:
    flow = _options_flow({CONF_SOC_MODE: SOC_MODE_BASIC, CONF_FOLLOW_HA_TIME_ZONE: True})

    asyncio.run(flow.async_step_init({CONF_SOC_MODE: SOC_MODE_BASIC}))

    assert flow._entry.data[CONF_FOLLOW_HA_TIME_ZONE] is True


def test_the_choice_survives_the_soc_step_of_a_first_switch_to_advanced() -> None:
    flow = _options_flow({CONF_SOC_MODE: SOC_MODE_BASIC})

    step = asyncio.run(
        flow.async_step_init({CONF_SOC_MODE: SOC_MODE_ADVANCED, CONF_FOLLOW_HA_TIME_ZONE: True})
    )
    assert step["step_id"] == "soc"
    asyncio.run(flow.async_step_soc({"battery_capacity": 60, "soc_correction": 7}))

    assert flow._entry.data[CONF_FOLLOW_HA_TIME_ZONE] is True
    assert flow._entry.data[CONF_SOC_MODE] == SOC_MODE_ADVANCED
