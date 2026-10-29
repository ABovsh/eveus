"""Opt-in: keep the charger's Time Zone on Home Assistant's current UTC offset.

The charger holds one whole-hour offset and never follows daylight saving
(issue #21): the REVE app and the Time Zone select are the only things that
move it, so the charger runs an hour off after every clock change.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock
from datetime import timedelta, timezone
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from conftest import OutageClock
from custom_components.eveus import issues as issues_module
from custom_components.eveus import select as select_module
from custom_components.eveus.const import (
    CLOCK_DRIFT_TRIGGER_POLLS,
    CONF_FOLLOW_HA_TIME_ZONE,
    TIME_ZONE_FOLLOW_POLLS,
    TIME_ZONE_FOLLOW_RETRY_POLLS,
)
from custom_components.eveus.issues import ClockDriftTracker, update_clock_drift_issue
from custom_components.eveus.switch import EveusFollowTimeZoneSwitch
from custom_components.eveus.snapshot import EveusSnapshot
from custom_components.eveus.time_zone_follow import (
    TimeZoneFollower,
    update_time_zone_follow,
)

HOUR = 3600


@pytest.mark.parametrize("change", ["off", "ha_offset", "offline", "failed_poll", "unchanged"])
def test_queued_follow_write_rechecks_current_intent_at_the_wire(change) -> None:
    """A queued automatic write must not undo a later user choice or use stale time."""
    from custom_components.eveus.common_command import CommandManager

    async def scenario():
        entry, updater, follower = _Entry(), _Updater(3), TimeZoneFollower()
        manager = CommandManager(updater)
        updater.send_command = manager.send_command
        manager._post_command = AsyncMock(return_value=True)
        await manager._lock.acquire()
        queued = asyncio.Event()
        acquire = manager._lock.acquire

        async def queued_acquire(priority=1):
            queued.set()
            await acquire(priority)

        manager._lock.acquire = queued_acquire
        for _ in range(TIME_ZONE_FOLLOW_POLLS):
            update_time_zone_follow(None, entry, updater, follower)
        task = asyncio.create_task(entry.tasks.pop())
        await queued.wait()
        if change == "off":
            entry.data.pop(CONF_FOLLOW_HA_TIME_ZONE)
        elif change == "ha_offset":
            _set_ha_offset(4 * HOUR)
        elif change == "offline":
            updater.available = False
        elif change == "failed_poll":
            updater.last_update_success = False
        manager._lock.release()
        await task
        if change == "unchanged":
            manager._post_command.assert_awaited_once_with("timeZone", 2, None)
        else:
            manager._post_command.assert_not_awaited()
        assert follower.sending is False
        assert manager.consecutive_failures == 0

    asyncio.run(scenario())


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

    def __init__(self, *, follow: bool = True) -> None:
        self.data: dict = {CONF_FOLLOW_HA_TIME_ZONE: True} if follow else {}
        self.entry_id = "entry-1"
        self.tasks: list = []
        self.task_args: list = []

    def async_create_background_task(self, hass, target, name, eager_start=True):
        self.task_args.append((hass, name))
        self.tasks.append(target)
        return None


class _Updater(OutageClock):
    host = "192.168.3.39"

    def __init__(self, charger_zone: int | None, *, available: bool = True, send=None) -> None:
        self.available = available
        self.last_update_success = available
        self.data = {}
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


def test_the_listener_does_nothing_while_the_switch_is_off() -> None:
    updater, entry = _Updater(3), _Entry(follow=False)

    _drive(updater, TimeZoneFollower(), entry, TIME_ZONE_FOLLOW_POLLS * 4)

    assert updater.sent == []
    assert entry.tasks == []


def test_turning_the_switch_on_starts_from_a_clean_count() -> None:
    """Polls counted while it was off must not make the first write immediate."""
    updater, entry, follower = _Updater(3), _Entry(follow=False), TimeZoneFollower()
    _drive(updater, follower, entry, TIME_ZONE_FOLLOW_POLLS * 4)

    entry.data[CONF_FOLLOW_HA_TIME_ZONE] = True
    for _ in range(TIME_ZONE_FOLLOW_POLLS - 1):
        update_time_zone_follow(None, entry, updater, follower)

    assert entry.tasks == []
    update_time_zone_follow(None, entry, updater, follower)
    assert len(entry.tasks) == 1


def test_turning_the_switch_off_and_on_again_restarts_the_pause() -> None:
    updater, entry, follower = _Updater(3), _Entry(), TimeZoneFollower()
    _drive(updater, follower, entry, TIME_ZONE_FOLLOW_POLLS)
    assert updater.sent == [("timeZone", 2)]

    entry.data.pop(CONF_FOLLOW_HA_TIME_ZONE)
    update_time_zone_follow(None, entry, updater, follower)
    entry.data[CONF_FOLLOW_HA_TIME_ZONE] = True
    _drive(updater, follower, entry, TIME_ZONE_FOLLOW_POLLS)

    assert updater.sent == [("timeZone", 2), ("timeZone", 2)]


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


def test_the_follower_starts_idle_with_plain_counters() -> None:
    follower = TimeZoneFollower()

    assert follower.sending is False
    assert follower._polls_to_wait == 0
    follower._polls_to_wait = 5
    follower.reset()
    assert follower._polls_to_wait == 0


def test_an_agreeing_poll_clears_the_pause_to_zero() -> None:
    follower = TimeZoneFollower()
    for _ in range(TIME_ZONE_FOLLOW_POLLS):
        follower.evaluate(_poll(3), 2 * HOUR)
    assert follower._polls_to_wait == TIME_ZONE_FOLLOW_RETRY_POLLS

    follower.evaluate(_poll(2), 2 * HOUR)

    assert follower._polls_to_wait == 0


def test_the_write_task_is_named_and_bound_to_hass() -> None:
    hass = object()
    updater, entry = _Updater(3), _Entry()
    follower = TimeZoneFollower()

    for _ in range(TIME_ZONE_FOLLOW_POLLS):
        update_time_zone_follow(hass, entry, updater, follower)

    assert entry.task_args == [(hass, "eveus_follow_time_zone")]
    for coro in entry.tasks:
        coro.close()


def test_either_an_unavailable_updater_or_a_failed_poll_blocks_the_write() -> None:
    for available, success in ((True, False), (False, True)):
        updater, entry = _Updater(3), _Entry()
        updater.available, updater.last_update_success = available, success

        _drive(updater, TimeZoneFollower(), entry, TIME_ZONE_FOLLOW_POLLS)

        assert updater.sent == []


def _debug_messages(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name.endswith("time_zone_follow")]


def test_a_refused_write_is_logged_once_per_interval(caplog, monkeypatch) -> None:
    from custom_components.eveus import time_zone_follow as module

    monkeypatch.setattr(module, "_LOG", module.RateLog())

    async def refused() -> bool:
        return False

    caplog.set_level("DEBUG")
    for _ in range(3):
        asyncio.run(module._write(_Updater(3, send=refused), TimeZoneFollower(), 2))

    messages = _debug_messages(caplog)
    assert sum("did not accept" in m for m in messages) == 1
    assert not any("follow failed" in m for m in messages)


def test_refused_and_failed_writes_are_rate_limited_separately(caplog, monkeypatch) -> None:
    from custom_components.eveus import time_zone_follow as module

    monkeypatch.setattr(module, "_LOG", module.RateLog())

    async def refused() -> bool:
        return False

    async def broken() -> bool:
        raise RuntimeError("x")

    caplog.set_level("DEBUG")
    asyncio.run(module._write(_Updater(3, send=refused), TimeZoneFollower(), 2))
    asyncio.run(module._write(_Updater(3, send=broken), TimeZoneFollower(), 2))
    asyncio.run(module._write(_Updater(3, send=broken), TimeZoneFollower(), 2))

    messages = _debug_messages(caplog)
    assert sum("did not accept" in m for m in messages) == 1
    assert sum("follow failed: RuntimeError" in m for m in messages) == 1


# --- the switch ----------------------------------------------------------------


class _ConfigEntries:
    def __init__(self) -> None:
        self.updates: list[dict] = []
        self.reloads: list[str] = []

    def async_update_entry(self, entry, *, data):
        self.updates.append(data)
        entry.data = data

    async def async_reload(self, entry_id):
        self.reloads.append(entry_id)


def _switch(*, follow: bool = False, available: bool = True):
    updater = _Updater(2, available=available)
    entry = _Entry(follow=follow)
    entry.data["host"] = "192.168.3.39"
    hass = SimpleNamespace(config_entries=_ConfigEntries())
    switch = EveusFollowTimeZoneSwitch(updater, entry, 1)
    switch.hass = hass
    switch.async_write_ha_state = lambda: None
    return switch, entry, hass


def test_the_switch_is_off_until_asked() -> None:
    switch, _, _ = _switch()

    assert switch.is_on is False


def test_the_switch_shows_what_the_entry_stores() -> None:
    switch, _, _ = _switch(follow=True)

    assert switch.is_on is True


def test_the_switch_is_named_and_identified_for_the_device_page() -> None:
    switch, _, _ = _switch()

    assert switch.unique_id == "eveus_follow_ha_time_zone"
    assert switch.entity_category.value == "config"


def test_the_switch_belongs_to_its_own_device_number() -> None:
    second = EveusFollowTimeZoneSwitch(_Updater(2), _Entry(), 2)

    assert second._device_number == 2
    assert EveusFollowTimeZoneSwitch(_Updater(2), _Entry())._device_number == 1


def test_turning_off_drops_the_key_instead_of_storing_false() -> None:
    switch, entry, hass = _switch(follow=True)

    asyncio.run(switch.async_turn_off())

    assert CONF_FOLLOW_HA_TIME_ZONE not in hass.config_entries.updates[-1]


def test_turning_off_a_switch_that_is_already_off_is_harmless() -> None:
    switch, entry, hass = _switch(follow=False)

    asyncio.run(switch.async_turn_off())

    assert CONF_FOLLOW_HA_TIME_ZONE not in hass.config_entries.updates[-1]


def test_the_switch_stays_usable_while_the_charger_is_off() -> None:
    """It is a setting of the integration, and the charger is mostly powered off."""
    switch, _, _ = _switch(available=False)

    assert switch.available is True


def test_turning_on_stores_the_choice_without_reloading() -> None:
    switch, entry, hass = _switch()

    asyncio.run(switch.async_turn_on())

    assert entry.data[CONF_FOLLOW_HA_TIME_ZONE] is True
    assert entry.data["host"] == "192.168.3.39"
    assert switch.is_on is True
    assert hass.config_entries.reloads == []


def test_turning_off_removes_the_key_and_keeps_the_rest() -> None:
    switch, entry, hass = _switch(follow=True)

    asyncio.run(switch.async_turn_off())

    assert CONF_FOLLOW_HA_TIME_ZONE not in entry.data
    assert entry.data["host"] == "192.168.3.39"
    assert switch.is_on is False
    assert hass.config_entries.reloads == []


def test_toggling_rebases_on_the_live_entry_data() -> None:
    """A reconfigure finishing meanwhile must not be rolled back."""
    switch, entry, _ = _switch()
    entry.data = {**entry.data, "host": "192.168.3.99"}

    asyncio.run(switch.async_turn_on())

    assert entry.data["host"] == "192.168.3.99"


def test_the_switch_publishes_its_new_state() -> None:
    switch, _, _ = _switch()
    published: list[bool] = []
    switch.async_write_ha_state = lambda: published.append(switch.is_on)

    asyncio.run(switch.async_turn_on())

    assert published == [True]


# --- the Time Zone select ------------------------------------------------------


def _select(*, follow: bool):
    updater = _Updater(3)
    updater.config_entry = None
    updater.host = "192.168.3.39"
    updater.snapshot = _poll(3)
    updater.async_add_listener = lambda *a, **k: (lambda: None)
    select = select_module.EveusTimeZoneSelect(updater, 1, _Entry(follow=follow))
    select.async_write_ha_state = lambda: None
    return select, updater


def test_a_manual_time_zone_is_refused_while_following_home_assistant() -> None:
    select, updater = _select(follow=True)

    with pytest.raises(HomeAssistantError):
        asyncio.run(select.async_select_option("+5"))

    assert updater.sent == []


def test_the_refusal_tells_the_user_how_to_proceed() -> None:
    select, _ = _select(follow=True)

    with pytest.raises(HomeAssistantError, match=r"^Follow HA Time Zone is on: turn it off to set the Time Zone by hand$"):
        asyncio.run(select.async_select_option("+5"))


def test_the_select_setup_hands_the_entry_to_the_time_zone_select() -> None:
    updater = _Updater(3)
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(updater=updater, device_number=1), data={}
    )
    added: list = []

    asyncio.run(select_module.async_setup_entry(object(), entry, added.extend))

    zone = next(e for e in added if e.ENTITY_NAME == "Time Zone")
    assert zone._entry is entry


def test_a_manual_time_zone_works_when_not_following() -> None:
    select, updater = _select(follow=False)

    asyncio.run(select.async_select_option("+5"))

    assert updater.sent == [("timeZone", 5)]


@pytest.mark.parametrize("enable_follow", [True, False])
def test_a_queued_manual_zone_respects_following_enabled_before_transmission(enable_follow) -> None:
    from custom_components.eveus.common_command import CommandManager

    async def scenario():
        select, updater = _select(follow=False)
        manager = CommandManager(updater)
        updater.send_command = manager.send_command
        manager._post_command = AsyncMock(return_value=True)
        await manager._lock.acquire()
        queued = asyncio.Event()
        acquire = manager._lock.acquire

        async def queued_acquire(priority=1):
            queued.set()
            await acquire(priority)

        manager._lock.acquire = queued_acquire
        task = asyncio.create_task(select.async_select_option("+5"))
        await queued.wait()
        if enable_follow:
            select._entry.data[CONF_FOLLOW_HA_TIME_ZONE] = True
        manager._lock.release()
        result, = await asyncio.gather(task, return_exceptions=True)
        if enable_follow:
            manager._post_command.assert_not_awaited()
            assert isinstance(result, HomeAssistantError)
            # Nothing was sent: the reason is the switch, not the charger.
            assert "Follow HA Time Zone is on" in str(result)
            assert select.current_option == "+3"
        else:
            manager._post_command.assert_awaited_once_with("timeZone", 5, None)
            assert result is None
        assert manager.consecutive_failures == 0

    asyncio.run(scenario())


def test_manual_zone_retry_is_cancelled_when_following_starts() -> None:
    from custom_components.eveus.common_command import CommandManager

    async def scenario():
        select, updater = _select(follow=False)
        manager = CommandManager(updater)
        updater.send_command = manager.send_command
        manager._post_command = AsyncMock(side_effect=[TimeoutError(), True])

        async def enable_follow(_):
            select._entry.data[CONF_FOLLOW_HA_TIME_ZONE] = True

        manager._sleep_backoff = enable_follow
        with pytest.raises(HomeAssistantError, match="Follow HA Time Zone is on"):
            await select.async_select_option("+5")
        manager._post_command.assert_awaited_once_with("timeZone", 5, None)
        assert select.current_option == "+3"
        assert manager.consecutive_failures == 0

    asyncio.run(scenario())


def test_the_select_still_shows_the_chargers_offset_while_following() -> None:
    select, _ = _select(follow=True)

    assert select.current_option == "+3"


# --- the repair notice ---------------------------------------------------------


def _drifted_poll() -> EveusSnapshot:
    """Charger on +3 with the right UTC time while HA is on +2: one hour ahead."""
    return EveusSnapshot.parse(
        {"systemTime": int(time.time()) + 3 * HOUR, "timeZone": 3}, None
    )


def test_the_tracker_waits_longer_when_the_follower_is_fixing_it() -> None:
    tracker = ClockDriftTracker()
    needed = TIME_ZONE_FOLLOW_POLLS + CLOCK_DRIFT_TRIGGER_POLLS

    results = [tracker.evaluate(_drifted_poll(), following=True) for _ in range(needed)]

    assert results[:-1] == [None] * (needed - 1)
    assert results[-1] is True


def test_the_tracker_does_not_wait_when_the_follower_is_off() -> None:
    tracker = ClockDriftTracker()

    results = [
        tracker.evaluate(_drifted_poll(), following=False)
        for _ in range(CLOCK_DRIFT_TRIGGER_POLLS)
    ]

    assert results[-1] is True


def test_a_wrong_clock_that_is_not_the_offset_is_not_delayed_by_the_follower() -> None:
    """Writing the offset cannot fix a clock that is minutes off."""
    tracker = ClockDriftTracker()
    off = EveusSnapshot.parse(
        {"systemTime": int(time.time()) + 2 * HOUR + 900, "timeZone": 2}, None
    )

    results = [
        tracker.evaluate(off, following=True) for _ in range(CLOCK_DRIFT_TRIGGER_POLLS)
    ]

    assert results[-1] is True


class _IssueRecorder:
    def __init__(self, monkeypatch) -> None:
        self.created: list[dict] = []
        monkeypatch.setattr(
            issues_module.ir,
            "async_create_issue",
            lambda hass, domain, issue_id, **kw: self.created.append(kw),
        )
        monkeypatch.setattr(issues_module.ir, "async_delete_issue", lambda *a, **k: None)


def _issue_updater() -> SimpleNamespace:
    updater = SimpleNamespace(available=True, last_update_success=True)
    updater.snapshot = _drifted_poll()
    return updater


def test_the_notice_asks_to_set_or_follow_while_the_switch_is_off(monkeypatch) -> None:
    recorder = _IssueRecorder(monkeypatch)
    entry, updater, tracker = _Entry(follow=False), _issue_updater(), ClockDriftTracker()

    for _ in range(CLOCK_DRIFT_TRIGGER_POLLS):
        update_clock_drift_issue(None, entry, updater, tracker)

    assert [c["translation_key"] for c in recorder.created] == ["clock_drift_timezone"]


def test_the_notice_says_following_is_not_working_while_the_switch_is_on(monkeypatch) -> None:
    recorder = _IssueRecorder(monkeypatch)
    entry, updater, tracker = _Entry(follow=True), _issue_updater(), ClockDriftTracker()

    for _ in range(TIME_ZONE_FOLLOW_POLLS + CLOCK_DRIFT_TRIGGER_POLLS - 1):
        update_clock_drift_issue(None, entry, updater, tracker)
    # Held back while the follower is still expected to fix it.
    assert recorder.created == []
    update_clock_drift_issue(None, entry, updater, tracker)

    assert [c["translation_key"] for c in recorder.created] == [
        "clock_drift_timezone_following"
    ]
    assert recorder.created[0]["translation_placeholders"] == {
        "hours": str(tracker.hours)
    }


def test_whole_hours_in_the_clock_itself_ask_for_sync_while_following(monkeypatch) -> None:
    """The charger already holds HA's offset, so the follower has nothing to
    write: the hours are in its clock, which Sync Time fixes at once."""
    recorder = _IssueRecorder(monkeypatch)
    entry, tracker = _Entry(follow=True), ClockDriftTracker()
    updater = SimpleNamespace(available=True, last_update_success=True)
    updater.snapshot = EveusSnapshot.parse(
        {"systemTime": int(time.time()) + 3 * HOUR, "timeZone": 2}, None
    )

    for _ in range(CLOCK_DRIFT_TRIGGER_POLLS):
        update_clock_drift_issue(None, entry, updater, tracker)

    assert [c["translation_key"] for c in recorder.created] == ["clock_drift"]
    assert recorder.created[0]["translation_placeholders"] is None


def test_turning_the_switch_on_rewrites_a_notice_that_is_already_showing(monkeypatch) -> None:
    recorder = _IssueRecorder(monkeypatch)
    entry, updater, tracker = _Entry(follow=False), _issue_updater(), ClockDriftTracker()
    for _ in range(CLOCK_DRIFT_TRIGGER_POLLS):
        update_clock_drift_issue(None, entry, updater, tracker)
    recorder.created.clear()

    entry.data[CONF_FOLLOW_HA_TIME_ZONE] = True
    for _ in range(CLOCK_DRIFT_TRIGGER_POLLS):
        update_clock_drift_issue(None, entry, updater, tracker)

    assert [c["translation_key"] for c in recorder.created] == [
        "clock_drift_timezone_following"
    ]
