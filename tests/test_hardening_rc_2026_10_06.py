"""Regressions for the October 6 RC round: replaced setpoint writes."""
import asyncio

import pytest
from homeassistant.exceptions import HomeAssistantError

from test_setpoint_number import ENERGY, _make


def _queue_two_writes(ent, order, *, newest_cancelled=False):
    """Queue 20 behind the entity lock, replace it with 30, then let both run."""

    async def scenario():
        await ent._command_lock.acquire()
        older = asyncio.create_task(ent.async_set_native_value(20))
        await asyncio.sleep(0)
        newest = asyncio.create_task(ent.async_set_native_value(30))
        await asyncio.sleep(0)
        older.add_done_callback(lambda _: order.append("older done"))
        if newest_cancelled:
            newest.cancel()
        ent._command_lock.release()
        return await asyncio.gather(older, newest, return_exceptions=True)

    return asyncio.run(scenario())


def test_replaced_write_returns_only_after_the_newest_write_is_sent():
    ent, updater = _make(ENERGY)

    async def send(command, value, *, preflight):
        await asyncio.sleep(0.01)  # a real POST takes time; the older call must still wait
        order.append(("sent", value))
        return preflight()

    order = []
    updater.send_command.side_effect = send
    results = _queue_two_writes(ent, order)

    assert results == [None, None]
    assert order == [("sent", 30000), "older done"]


def test_replaced_write_reports_the_newest_write_failure():
    ent, updater = _make(ENERGY)
    updater.send_command.return_value = False

    older, newest = _queue_two_writes(ent, [])

    assert isinstance(newest, HomeAssistantError)
    assert isinstance(older, HomeAssistantError)
    updater.send_command.assert_awaited_once()


def test_replaced_write_fails_cleanly_when_the_newest_write_is_cancelled():
    ent, updater = _make(ENERGY)

    older, newest = _queue_two_writes(ent, [], newest_cancelled=True)

    assert isinstance(newest, asyncio.CancelledError)
    # The older caller was not cancelled; it learns its value was not applied.
    assert isinstance(older, HomeAssistantError)
    updater.send_command.assert_not_awaited()


@pytest.mark.parametrize("newest_ok", [True, False])
def test_write_replaced_at_the_wire_shares_the_newest_outcome(newest_ok):
    ent, updater = _make(ENERGY)
    updater.data = {"energyLimit": 10}

    async def scenario():
        reached_wire, release = asyncio.Event(), asyncio.Event()

        async def hold_at_wire(command, value, *, preflight):
            reached_wire.set()
            await release.wait()
            return preflight()

        updater.send_command.side_effect = hold_at_wire
        older = asyncio.create_task(ent.async_set_native_value(20))
        await reached_wire.wait()
        newest = asyncio.create_task(ent.async_set_native_value(30))
        await asyncio.sleep(0)
        updater.send_command.side_effect = None
        updater.send_command.return_value = newest_ok
        release.set()
        return await asyncio.gather(older, newest, return_exceptions=True)

    older, newest = asyncio.run(scenario())

    if newest_ok:
        assert (older, newest) == (None, None)
    else:
        assert isinstance(newest, HomeAssistantError)
        assert isinstance(older, HomeAssistantError)


def test_waiter_survives_the_write_it_waits_on_being_cancelled():
    """A caller sharing a replaced write's outcome still finishes when that
    write's own caller is cancelled while it waits for an even newer value."""
    ent, updater = _make(ENERGY)

    async def scenario():
        reached_wire, release = asyncio.Event(), asyncio.Event()

        async def hold_at_wire(command, value, *, preflight):
            reached_wire.set()
            await release.wait()
            return preflight()

        updater.send_command.side_effect = hold_at_wire
        lock = ent._command_lock
        await lock.acquire()
        older = asyncio.create_task(ent.async_set_native_value(20))
        await asyncio.sleep(0)
        # Stands in for the replaced writes queued between the two: the older
        # call has already taken `middle` as the newest write when `newest`
        # arrives, and `middle` only reaches the lock after that.
        gate = asyncio.Event()

        async def hold():
            async with lock:
                await gate.wait()

        holder = asyncio.create_task(hold())
        await asyncio.sleep(0)
        middle = asyncio.create_task(ent.async_set_native_value(25))
        await asyncio.sleep(0)
        lock.release()
        for _ in range(5):
            await asyncio.sleep(0)
        newest = asyncio.create_task(ent.async_set_native_value(30))
        await asyncio.sleep(0)
        gate.set()
        await holder
        await reached_wire.wait()
        middle.cancel()
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.wait_for(
            asyncio.gather(older, middle, newest, return_exceptions=True), 1
        )
        return results

    older, middle, newest = asyncio.run(scenario())

    assert isinstance(middle, asyncio.CancelledError)
    assert newest is None
    # The newest value was applied, so the older call succeeded with it.
    assert older is None
    updater.send_command.assert_awaited_once()


def test_cancelling_a_replaced_queued_write_does_not_fail_the_older_call():
    """A replaced write cancelled while it waits for the lock defers to the
    newer write, so the call it replaced reports the value actually applied."""
    ent, updater = _make(ENERGY)

    async def scenario():
        lock = ent._command_lock
        await lock.acquire()
        older = asyncio.create_task(ent.async_set_native_value(20))
        await asyncio.sleep(0)
        gate = asyncio.Event()

        async def hold():
            async with lock:
                await gate.wait()

        holder = asyncio.create_task(hold())
        await asyncio.sleep(0)
        middle = asyncio.create_task(ent.async_set_native_value(25))
        await asyncio.sleep(0)
        lock.release()
        for _ in range(5):
            await asyncio.sleep(0)
        newest = asyncio.create_task(ent.async_set_native_value(30))
        await asyncio.sleep(0)
        middle.cancel()  # still queued behind `holder`
        await asyncio.sleep(0)
        gate.set()
        await holder
        return await asyncio.wait_for(
            asyncio.gather(older, middle, newest, return_exceptions=True), 1
        )

    older, middle, newest = asyncio.run(scenario())

    assert isinstance(middle, asyncio.CancelledError)
    assert newest is None
    assert older is None
    updater.send_command.assert_awaited_once()
