"""Audit 2026-11-12 #12: a poll that overlapped a write cannot undo it.

A REVE poll and a command hold different locks, so a measurement read can
start before a write and land after it. Its older limit value or charging
command must not replace what the write just set: the next toggle would apply
the old cap, or One Charge off would release a Stop just applied.
"""
import asyncio
from copy import deepcopy
import pytest

from custom_components.eveus.reve import MEASUREMENTS_QUERY, MEASUREMENTS_AND_SETTINGS_QUERY
from test_reve_firmware import _reve_main
from test_reve_graphql_writes import _NullRefusingCharger, _WritableCharger, _inputs, _known_charging


@pytest.mark.parametrize("key,flag,field,old,new,factor", [
    ("energyLimit", "energyLimitS", "energyLimit", 50000, 20000, 1000),
    ("timeLimit", "timeLimitS", "timeLimit", 7200, 3600, 1),
    ("moneyLimit", "moneyLimitS", "costLimit", 200, 100, 1),
], ids=["energy", "time", "cost"])
@pytest.mark.parametrize("old_reply_arrives_last", [True, False], ids=["race", "ordered-control"])
def test_older_poll_must_not_restore_the_previous_cap_for_reenable(
    monkeypatch, key, flag, field, old, new, factor, old_reply_arrives_last
):
    charger = _NullRefusingCharger(_reve_main())
    charger.station["session"][field] = old
    charger, updater = _known_charging(monkeypatch, charger)
    monkeypatch.setattr(updater, "_schedule_post_command_refresh", lambda: None)
    assert updater.snapshot.get(key) == old / factor

    async def scenario():
        old_read = asyncio.Event()
        release_read = asyncio.Event()
        api = updater._reve
        original = api._station
        delayed = False

        async def delayed_station(document):
            nonlocal delayed
            reply = deepcopy(await original(document))
            if not delayed and document in (MEASUREMENTS_QUERY, MEASUREMENTS_AND_SETTINGS_QUERY):
                delayed = True
                old_read.set()
                await release_read.wait()
            return reply

        monkeypatch.setattr(api, "_station", delayed_station)
        poll = asyncio.create_task(updater._async_update_data())
        await asyncio.wait_for(old_read.wait(), 2)
        if not old_reply_arrives_last:
            release_read.set()
            await asyncio.wait_for(poll, 2)
        assert await updater.send_command(key, new)
        assert charger.station["session"][field] == new
        assert api._limit_values[key] == new / factor
        release_read.set()
        await asyncio.wait_for(poll, 2)
        # No fresh poll occurs between these two ordinary user operations.
        assert await updater.send_command(flag, 0)
        assert charger.station["session"][field] is None
        assert await updater.send_command(flag, 1)

    asyncio.run(scenario())
    assert _inputs(charger)[-1] == {"session": {field: new}}, (
        "An older read replaced the newer acknowledged cap; re-enable sends "
        f"{_inputs(charger)[-1]}"
    )


@pytest.mark.parametrize("old_reply_arrives_last", [True, False], ids=["race", "ordered-control"])
def test_older_poll_must_not_let_one_charge_off_release_a_new_stop(
    monkeypatch, old_reply_arrives_last
):
    charger = _WritableCharger(_reve_main())
    charger.charging_command = 0
    charger, updater = _known_charging(monkeypatch, charger)
    monkeypatch.setattr(updater, "_schedule_post_command_refresh", lambda: None)
    assert updater._reve._extras.charging_command == 0

    async def scenario():
        old_read = asyncio.Event()
        release_read = asyncio.Event()
        api = updater._reve
        original = api._station
        delayed = False

        async def delayed_station(document):
            nonlocal delayed
            reply = deepcopy(await original(document))
            if not delayed and document in (MEASUREMENTS_QUERY, MEASUREMENTS_AND_SETTINGS_QUERY):
                delayed = True
                old_read.set()
                await release_read.wait()
            return reply

        monkeypatch.setattr(api, "_station", delayed_station)
        poll = asyncio.create_task(updater._async_update_data())
        await asyncio.wait_for(old_read.wait(), 2)
        if not old_reply_arrives_last:
            release_read.set()
            await asyncio.wait_for(poll, 2)
        assert await updater.send_command("evseEnabled", 1)
        assert charger.charging_command == 2
        assert api._extras.charging_command == 2
        release_read.set()
        await asyncio.wait_for(poll, 2)
        assert await updater.send_command("oneCharge", 0)

    asyncio.run(scenario())
    assert _inputs(charger) == [{"chargingCommand": 2}], (
        "Turning One Charge off must leave an acknowledged Stop alone; got "
        f"{_inputs(charger)}"
    )


@pytest.mark.parametrize("old_reply_arrives_last", [True, False], ids=["race", "ordered-control"])
def test_older_settings_read_must_not_restore_the_schedules_just_written(
    monkeypatch, old_reply_arrives_last
):
    """A poll that also reads the settings (due every five minutes) can start
    before a schedule write and land after it. Its older schedules must not
    replace the written ones: the read also restarts the settings clock, so
    the switch and card would show the old schedule for five minutes."""
    charger = _WritableCharger(_reve_main())
    charger, updater = _known_charging(monkeypatch, charger)
    monkeypatch.setattr(updater, "_schedule_post_command_refresh", lambda: None)
    api = updater._reve
    before = bool(api._extras.schedules[0]["enabled"])

    async def scenario():
        old_read = asyncio.Event()
        release_read = asyncio.Event()
        original = api._station
        delayed = False

        async def delayed_station(document):
            nonlocal delayed
            reply = deepcopy(await original(document))
            if not delayed and document == MEASUREMENTS_AND_SETTINGS_QUERY:
                delayed = True
                old_read.set()
                await release_read.wait()
            return reply

        monkeypatch.setattr(api, "_station", delayed_station)
        api._fetched_at = None  # the settings are due with this poll
        poll = asyncio.create_task(updater._async_update_data())
        await asyncio.wait_for(old_read.wait(), 2)
        if not old_reply_arrives_last:
            release_read.set()
            await asyncio.wait_for(poll, 2)
        assert await updater.send_command("sh1Enabled", 0 if before else 1)
        release_read.set()
        await asyncio.wait_for(poll, 2)

    asyncio.run(scenario())
    assert bool(api._extras.schedules[0]["enabled"]) is (not before), (
        "An older settings read put the previous schedule back"
    )
