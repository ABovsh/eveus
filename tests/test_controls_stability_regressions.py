"""Regression tests for control writes, reconciliation and restore."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.eveus.binary_sensor import (
    EveusManualModeBinarySensor,
)
from custom_components.eveus.number import GLOBAL_LIMIT_NUMBERS
from custom_components.eveus.reve import ManualMode
from custom_components.eveus.soc_limit import EVENT_SOC_LIMIT_REACHED, SocLimitController
from custom_components.eveus.switch import BaseSwitchEntity, SWITCH_DESCRIPTIONS
from test_legacy_review_fixes import _number, _poll, _rig
from test_reve_firmware import _Response, _reve_main, _updater as _reve_updater
from test_reve_graphql_writes import _WritableCharger, _inputs, _known_charging
from test_reve_manual_mode import _plugged
from test_setpoint_number import _make
from test_soc_limit import _calc, _updater


@pytest.mark.parametrize(("key", "flag", "field", "before", "after"), [
    ("energyLimit", "energyLimitS", "energyLimit", 20000, 30000),
    ("timeLimit", "timeLimitS", "timeLimit", 1800, 3600),
    ("moneyLimit", "moneyLimitS", "costLimit", 20, 30),
], ids=["energy", "time", "cost"])
@pytest.mark.parametrize("enable", [True, False], ids=["enable", "disable"])
def test_reve_limit_edit_uses_latest_acknowledged_toggle(
    monkeypatch, key, flag, field, before, after, enable
):
    monkeypatch.setattr("custom_components.eveus.reve.time.monotonic", lambda: 1000.0)
    charger = _WritableCharger(_reve_main())
    charger.station["session"][field] = before
    charger, updater = _known_charging(monkeypatch, charger)
    monkeypatch.setattr(updater, "_schedule_post_command_refresh", lambda: None)

    async def scenario():
        if enable:
            assert await updater.send_command(flag, 0)
            await updater._async_update_data()
            charger.calls.clear()
        assert updater.snapshot.get_int(flag) == int(not enable)
        assert await updater.send_command(flag, int(enable))
        assert await updater.send_command(key, after)
        expected = after if enable else None
        assert charger.station["session"][field] == expected
        assert len(_inputs(charger)) == (2 if enable else 1)
        await updater._async_update_data()
        assert updater.snapshot.get_int(flag) == int(enable)
        assert updater.snapshot.get(key) == (after / 1000 if key == "energyLimit" else after)

    asyncio.run(scenario())


@pytest.mark.parametrize("failures", [1, 3], ids=["recovers", "exhausted"])
def test_reve_stop_release_retries_only_manual_mode_exit(monkeypatch, failures):
    class Charger(_WritableCharger):
        def post(self, url, **kwargs):
            if url.endswith("/graphql"):
                body = json.loads(kwargs["data"])
                if body["variables"].get("input") == {"chargingManualControl": False}:
                    if self.remaining_failures:
                        self.remaining_failures -= 1
                        self.calls.append(("/graphql", body))
                        return _Response(503, {})
            return super().post(url, **kwargs)

    monkeypatch.setattr("custom_components.eveus.reve.time.monotonic", lambda: 1000.0)
    charger = Charger(_reve_main())
    charger.remaining_failures = failures
    charger.charging_command = 2
    charger.station["chargingManualControl"] = True
    charger, updater = _known_charging(monkeypatch, charger)
    refreshes = []
    monkeypatch.setattr(updater, "_schedule_post_command_refresh", lambda: refreshes.append(True))
    stop = BaseSwitchEntity(updater, SWITCH_DESCRIPTIONS[0])
    monkeypatch.setattr(stop, "async_write_ha_state", lambda: None)

    async def scenario():
        if failures == 3:
            with pytest.raises(HomeAssistantError) as error:
                await stop.async_turn_off()
        else:
            await stop.async_turn_off()
        assert _inputs(charger) == (
            [{"chargingCommand": 1}]
            + [{"chargingManualControl": False}] * min(failures + 1, 3)
        )
        assert refreshes == [True]
        assert charger.charging_command == 1
        assert charger.station["chargingManualControl"] is (failures == 3)
        if failures == 3:
            assert "released Stop" in str(error.value)

    asyncio.run(scenario())


@pytest.mark.parametrize(("wifi_version", "encoded"), [
    ("GRW001A-R3.01.8", 100),
    ("GRW001A-R3.05.8", 10000),
    (None, 10000),
], ids=["3018", "3058", "main-version-only"])
async def test_r3018_energy_limit_requires_matching_wifi_version(monkeypatch, wifi_version, encoded):
    monkeypatch.setattr("custom_components.eveus.number.time.monotonic", lambda: 1000.0)
    updater, session, _ = _rig(monkeypatch)
    await _poll(updater, session, energyLimit=5, verFWMain="GRM070A-R3.01.8", verFWWifi=wifi_version)

    await _number(updater, "energyLimit").async_set_native_value(10)

    assert session.calls[-1]["data"] == f"pageevent=energyLimit&energyLimit={encoded}"


async def test_queued_energy_limit_rechecks_the_r3018_write_scale(monkeypatch):
    monkeypatch.setattr("custom_components.eveus.number.time.monotonic", lambda: 1000.0)
    updater, session, _ = _rig(monkeypatch)
    await _poll(updater, session, energyLimit=5, verFWMain="GRM070A-R3.01.8")
    number = _number(updater, "energyLimit")

    async with updater._command_manager._lock.hold(1):
        task = asyncio.create_task(number.async_set_native_value(10))
        await asyncio.sleep(0)
        assert not task.done()
        await _poll(
            updater, session, energyLimit=5, verFWMain="GRM070A-R3.01.8",
            verFWWifi="GRW001A-R3.01.8",
        )
    with pytest.raises(HomeAssistantError, match="firmware or maximum changed"):
        await task
    assert not any(call["url"].endswith("/pageEvent") for call in session.calls)


@pytest.mark.parametrize("live_command", [2, None], ids=["app-stopped", "unknown"])
def test_reve_one_charge_off_checks_current_charging_command(monkeypatch, live_command):
    monkeypatch.setattr("custom_components.eveus.reve.time.monotonic", lambda: 1000.0)
    charger = _WritableCharger(_reve_main())
    charger.charging_command = 0
    charger, updater = _known_charging(monkeypatch, charger)
    monkeypatch.setattr(updater, "_schedule_post_command_refresh", lambda: None)
    assert updater.snapshot.get_int("oneCharge") == 1

    charger.charging_command = live_command
    success = asyncio.run(updater.send_command("oneCharge", 0))

    assert _inputs(charger) == []
    assert charger.charging_command == live_command
    assert success is (live_command == 2)


async def test_stale_time_limit_read_does_not_confirm_a_nearby_setpoint(monkeypatch):
    monkeypatch.setattr("custom_components.eveus.number.time.monotonic", lambda: 1000.0)
    entity, updater = _make(GLOBAL_LIMIT_NUMBERS[0])
    updater.data = {"timeLimit": 3600}
    entity._handle_coordinator_update()
    await entity.async_set_native_value(61)
    assert updater.send_command.await_args.args == ("timeLimit", 3660)
    assert entity.native_value == 61
    entity.async_write_ha_state.reset_mock()

    entity._handle_coordinator_update()
    assert entity.native_value == 61
    assert entity._optimistic_value == 61
    updater.data = {"timeLimit": 3660}
    entity._handle_coordinator_update()
    assert entity.native_value == 61
    assert entity._optimistic_value is None
    entity.async_write_ha_state.assert_not_called()


async def test_number_displays_the_encoded_value(monkeypatch):
    monkeypatch.setattr("custom_components.eveus.number.time.monotonic", lambda: 1000.0)
    entity, updater = _make(GLOBAL_LIMIT_NUMBERS[2])
    await entity.async_set_native_value(10.5)

    assert updater.send_command.await_args.args == ("moneyLimit", 10)
    assert entity.native_value == 10


@pytest.mark.parametrize("stopped_state", [3, 5, 7], ids=["connected", "complete", "fault"])
@pytest.mark.parametrize("unplug_seen", [True, False], ids=["unplug", "counter-reset"])
async def test_soc_stop_latch_survives_a_resume_in_the_same_plug_cycle(stopped_state, unplug_seen):
    updater = _updater()
    events, tasks = [], []

    def spawn(coro):
        task = asyncio.create_task(coro)
        tasks.append(task)
        return task

    bus = SimpleNamespace(async_fire=lambda name, payload: events.append(name))
    controller = SocLimitController(SimpleNamespace(async_create_task=spawn, bus=bus), updater, _calc())
    controller.set_enabled(True)

    async def poll(state, stopped, seconds, energy=30):
        updater.data.update(
            state=state, evseEnabled=stopped, sessionTime=seconds, sessionEnergy=energy,
        )
        controller.process()
        if tasks:
            await tasks[-1]

    await poll(4, 0, 400)
    await poll(stopped_state, 1, 401)
    await poll(4, 0, 402)
    assert updater.send_command.await_count == 1
    assert events == [EVENT_SOC_LIMIT_REACHED]

    if unplug_seen:
        await poll(2, 0, 0, energy=0)
    await poll(4, 0, 300)
    await poll(3, 1, 301)
    assert updater.send_command.await_count == 2
    assert events == [EVENT_SOC_LIMIT_REACHED, EVENT_SOC_LIMIT_REACHED]


@pytest.mark.parametrize(("state", "elapsed", "reported", "expected"), [
    (4, 1800, None, True),
    (4, 120, None, False),
    (2, 0, None, False),
    (4, 1800, False, False),
], ids=["same-session", "younger-session", "unplugged", "explicit-off"])
def test_reve_manual_mode_restores_from_unavailable_state(
    monkeypatch, state, elapsed, reported, expected
):
    now = 2000.0
    monkeypatch.setattr("custom_components.eveus.reve.time.time", lambda: now)
    monotonic = 1000.0
    monkeypatch.setattr("custom_components.eveus.reve.time.monotonic", lambda: monotonic)
    charger = _WritableCharger(_reve_main())
    charger.station.pop("chargingManualControl", None)
    charger, updater = _known_charging(monkeypatch, charger)
    sensor = EveusManualModeBinarySensor(updater)
    monkeypatch.setattr(updater, "_schedule_post_command_refresh", lambda: None)

    assert asyncio.run(updater.send_command("evseEnabled", 1))
    now = 2100.0
    assert asyncio.run(updater.send_command("evseEnabled", 0))
    assert sensor.is_on is True
    now = 3000.0
    updater._record_failure(TimeoutError())
    monotonic = 1100.0
    assert sensor.available is False
    assert sensor.is_on is None
    extra = sensor.extra_restore_state_data
    assert extra is not None
    assert extra.as_dict() == {"manual_mode_started_at": 2000.0}

    now = 3500.0
    charger.station["chargingManualControl"] = reported
    _plugged(charger, state=state, session_time=elapsed)
    restarted = _reve_updater(monkeypatch, charger)
    asyncio.run(restarted._async_update_data())
    restored = EveusManualModeBinarySensor(restarted)
    restored.async_get_last_extra_data = AsyncMock(return_value=extra)
    asyncio.run(restored._async_restore_state(SimpleNamespace(state="unavailable")))

    assert restored.is_on is expected
    if expected:
        assert restored.extra_restore_state_data.as_dict() == extra.as_dict()


@pytest.mark.parametrize(("elapsed", "expected"), [(120, False), (1800, True)])
def test_reve_manual_mode_checks_age_when_the_first_session_counter_arrives(
    monkeypatch, elapsed, expected
):
    monkeypatch.setattr("custom_components.eveus.reve.time.time", lambda: 3500.0)
    manual = ManualMode()
    manual.observe({"state": 4})  # The startup /main fallback has no session counter.
    manual.restore(on_for=1500)
    assert manual.on is True

    manual.observe({"state": 4, "sessionTime": elapsed})

    assert manual.on is expected
