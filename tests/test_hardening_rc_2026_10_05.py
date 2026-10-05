"""Adversarial regressions for the October 5 RC and its interaction surface."""
import asyncio

import pytest

from test_meter_publication import make_meter
from test_soc_limit import _RecordingSession, _real_stack, _seed, _settle, _stop_posts
from test_setpoint_number import ENERGY, _make


@pytest.mark.parametrize("key", ["total_energy", "session_energy", "counter_a_cost"])
@pytest.mark.parametrize("missing", [None, "invalid", "absent"])
def test_missing_meter_flushes_tail_then_becomes_unavailable(monkeypatch, key, missing):
    entity, updater, clock, field = make_meter(monkeypatch, key)
    rows = []
    entity.async_write_ha_state = lambda: rows.append((entity.native_value, entity.available))
    entity._handle_coordinator_update()
    clock[0] = 150
    updater.data[field] = 11
    entity._handle_coordinator_update()
    if missing == "absent":
        updater.data.pop(field)
    else:
        updater.data[field] = missing
    entity._handle_coordinator_update()
    assert rows == [(10, True), (11, True), (None, False)]
    entity._handle_coordinator_update()
    assert len(rows) == 3
    updater.data[field] = 0
    entity._handle_coordinator_update()
    assert rows[-1] == (0, True)


def test_soc_event_uses_target_and_soc_at_transmission(monkeypatch):
    session = _RecordingSession(200)
    updater, ctrl, events = _real_stack(monkeypatch, session)

    async def scenario():
        lock = updater._command_manager._lock
        await lock.acquire()
        ctrl.process()
        await _settle()
        ctrl._calc.set_value("target_soc", 70)
        _seed(updater, {**updater.data, "sessionEnergy": 31})
        lock.release()
        await ctrl._stop_task
        _seed(updater, {**updater.data, "evseEnabled": 1})
        ctrl.process()

    asyncio.run(scenario())
    assert len(_stop_posts(session)) == 1
    assert len(events) == 1
    assert events[0][1]["soc"] == 82
    assert events[0][1]["target_soc"] == 70


@pytest.mark.parametrize("prior_failure", [False, True])
def test_manual_stop_cannot_confirm_soc_command_still_in_queue(monkeypatch, prior_failure):
    session = _RecordingSession(400, 200) if prior_failure else _RecordingSession(200)
    updater, ctrl, events = _real_stack(monkeypatch, session)

    async def scenario():
        if prior_failure:
            ctrl.process()
            await ctrl._stop_task
        lock = updater._command_manager._lock
        await lock.acquire()
        ctrl.process()
        await _settle()
        task = ctrl._stop_task
        _seed(updater, {**updater.data, "evseEnabled": 1})
        ctrl.process()
        lock.release()
        await task

    asyncio.run(scenario())
    assert len(_stop_posts(session)) == int(prior_failure)
    assert events == []


def test_latest_setpoint_replaces_value_waiting_in_shared_queue(monkeypatch):
    entity, updater = _make(ENERGY)
    session = _RecordingSession(200)
    wire_updater, _, _ = _real_stack(monkeypatch, session)
    updater.send_command = wire_updater.send_command

    async def scenario():
        lock = wire_updater._command_manager._lock
        await lock.acquire()
        first = asyncio.create_task(entity.async_set_native_value(20))
        await _settle()
        newest = asyncio.create_task(entity.async_set_native_value(40))
        await _settle()
        lock.release()
        await asyncio.gather(first, newest)

    asyncio.run(scenario())
    assert len(session.calls) == 1
    assert session.calls[0]["data"] == "pageevent=energyLimit&energyLimit=40000"
    assert entity.native_value == 40


@pytest.mark.parametrize("missing", ["target", "energy"])
def test_incomplete_soc_inputs_veto_without_counting_command_failure(monkeypatch, missing):
    session = _RecordingSession(200)
    updater, ctrl, events = _real_stack(monkeypatch, session)

    async def scenario():
        lock = updater._command_manager._lock
        await lock.acquire()
        ctrl.process()
        await _settle()
        if missing == "target":
            ctrl._calc.set_value("target_soc", None)
        else:
            _seed(updater, {k: v for k, v in updater.data.items() if k != "sessionEnergy"})
        lock.release()
        await ctrl._stop_task

    asyncio.run(scenario())
    assert _stop_posts(session) == []
    assert events == []
    assert updater._command_manager.consecutive_failures == 0
