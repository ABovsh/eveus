"""Pre-R3 (1.x/2.x) corner cases: queued writes, confirmation and plug events."""

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.eveus import common_command, common_network, switch
from custom_components.eveus.const import LEGACY_RAW_STATE_KEY
from custom_components.eveus.number import (
    GLOBAL_LIMIT_NUMBERS,
    SCHEDULE_LIMIT_NUMBERS,
    EveusCurrentNumber,
    EveusSetpointNumber,
    EveusSocketLimitedCurrentNumber,
)
from custom_components.eveus.select import EveusMinVoltageSelect

from test_common_network import _Response, _Session, _updater_with_bus

_CURRENTS = [
    ("currentSet", "currentSet"),
    ("sh1CurrentValue", "currentSchedule1"),
    ("sh2CurrentValue", "currentSchedule2"),
]
_FRACTIONAL_LIMITS = [
    ("moneyLimit", "moneyLimit"),
    ("sh1EnergyValue", "energySchedule1"),
    ("sh2EnergyValue", "energySchedule2"),
]
_FIXTURES = Path(__file__).with_name("fixtures")


def _main_fixture(filename="real_main_response.json"):
    return json.loads((_FIXTURES / filename).read_text())


def _rig(monkeypatch):
    updater, bus = _updater_with_bus()
    updater._model = "32A"
    session = _Session(_Response())
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda _: session)
    monkeypatch.setattr(updater, "_schedule_post_command_refresh", lambda: None)
    return updater, session, bus


async def _poll(updater, session, **changes):
    session.response.payload = {"state": 2, "currentSet": 12, "pilot": 2, **changes}
    updater.data = await updater._async_update_data()


async def _confirm(updater, session, **changes):
    session.response.payload = {"ESP_SW_version": 155, **changes}
    await updater.async_maybe_fetch_init_firmware()
    assert updater.is_legacy_firmware


def _number(updater, key):
    if key == "currentSet":
        entity = EveusCurrentNumber(updater, "32A", 1)
    else:
        desc = next(
            d for d in (*GLOBAL_LIMIT_NUMBERS, *SCHEDULE_LIMIT_NUMBERS) if d.command == key
        )
        cls = (
            EveusSocketLimitedCurrentNumber
            if desc.native_unit_of_measurement == "A"
            else EveusSetpointNumber
        )
        entity = cls(updater, desc, 1)
    entity.async_write_ha_state = Mock()
    return entity


def _switch_entry(updater):
    updater.update_interval = None
    return SimpleNamespace(
        runtime_data=SimpleNamespace(updater=updater, device_number=1),
        data={"soc_mode": "basic"},
        async_on_unload=Mock(),
    )


def _socket_switches(entities):
    return [
        entity for entity in entities
        if getattr(entity, "entity_description", None) is switch._HOUSEHOLD_SOCKET
    ]


async def test_socket_switch_waits_for_positive_init_evidence(monkeypatch):
    updater, session, _ = _rig(monkeypatch)
    await _poll(updater, session, restricted_mode=1)
    entities = []
    await switch.async_setup_entry(None, _switch_entry(updater), entities.extend)
    assert not _socket_switches(entities)
    await _confirm(updater, session)
    updater.async_update_listeners()
    assert len(_socket_switches(entities)) == 1


@pytest.mark.parametrize("filename", ["real_main_response.json", "reve_main_response.json"])
async def test_nonlegacy_restricted_mode_does_not_add_socket_switch(monkeypatch, filename):
    updater, session, _ = _rig(monkeypatch)
    await _poll(updater, session, **(_main_fixture(filename) | {"restricted_mode": 1}))
    entities = []
    await switch.async_setup_entry(None, _switch_entry(updater), entities.extend)
    assert not _socket_switches(entities)


@pytest.mark.parametrize("filename", ["real_main_response.json", "reve_main_response.json"])
@pytest.mark.parametrize("key", [key for key, _ in _CURRENTS])
async def test_nonlegacy_restricted_mode_keeps_model_current_maximum(monkeypatch, filename, key):
    updater, session, _ = _rig(monkeypatch)
    await _poll(updater, session, **(_main_fixture(filename) | {"restricted_mode": 1}))
    assert not updater.legacy_socket_limited
    entity = _number(updater, key)
    assert entity.native_max_value == 32
    if filename == "real_main_response.json":
        await entity.async_set_native_value(25)
        assert session.calls[-1]["data"] == f"pageevent={key}&{key}=25"


@pytest.mark.parametrize(("key", "wire"), _CURRENTS)
async def test_accepted_socket_restriction_changes_current_bound_until_definite_readback(
    monkeypatch, key, wire,
):
    updater, session, _ = _rig(monkeypatch)
    await _poll(updater, session, restricted_mode=0)
    await _confirm(updater, session)
    number = _number(updater, key)
    updater.update_interval = None
    updater.async_add_listener(number._handle_coordinator_update)
    updater.async_update_listeners()
    number.async_write_ha_state.reset_mock()
    socket = switch.BaseSwitchEntity(updater, switch._HOUSEHOLD_SOCKET)
    socket.async_write_ha_state = Mock()
    clock = 1000.0
    monkeypatch.setattr(common_command, "time", SimpleNamespace(monotonic=lambda: clock))

    await socket.async_turn_on()
    assert socket.is_on is True
    assert number.native_max_value == 16
    number.async_write_ha_state.assert_called_once()
    updater.async_update_listeners()
    number.async_write_ha_state.assert_called_once()
    clock += 1
    await number.async_set_native_value(25)
    assert session.calls[-1]["data"] == f"{wire}=16"

    await _poll(updater, session, restricted_mode=1)
    for changes in ({}, {"restricted_mode": 2}):
        await _poll(updater, session, **changes)
        assert number.native_max_value == 16
    clock += 1
    await socket.async_turn_off()
    await _poll(updater, session)
    assert number.native_max_value == 16
    await _poll(updater, session, restricted_mode=0)
    assert number.native_max_value == 32


async def test_repeated_accepted_socket_restriction_does_not_notify_listeners(monkeypatch):
    updater, session, _ = _rig(monkeypatch)
    await _poll(updater, session, restricted_mode=1)
    await _confirm(updater, session)
    updater.update_interval = None
    listener = Mock()
    updater.async_add_listener(listener)
    assert _number(updater, "currentSet").native_max_value == 16
    assert await updater.send_command("restricted_mode", 1)
    listener.assert_not_called()


async def test_classic_r3_accepted_restricted_mode_command_keeps_current_maximum(monkeypatch):
    updater, session, _ = _rig(monkeypatch)
    await _poll(updater, session, **(_main_fixture() | {"restricted_mode": 0}))
    listener = Mock()
    updater.update_interval = None
    updater.async_add_listener(listener)
    assert await updater.send_command("restricted_mode", 1)
    listener.assert_not_called()
    assert _number(updater, "currentSet").native_max_value == 32


async def test_rejected_socket_restriction_does_not_cap_current(monkeypatch):
    updater, session, _ = _rig(monkeypatch)
    await _poll(updater, session, restricted_mode=0)
    await _confirm(updater, session)

    async def reject(*args, **kwargs):
        return False

    monkeypatch.setattr(updater._command_manager, "send_command", reject)
    assert not await updater.send_command("restricted_mode", 1)
    assert _number(updater, "currentSet").native_max_value == 32


async def test_unrelated_legacy_switch_command_does_not_cap_current(monkeypatch):
    updater, session, _ = _rig(monkeypatch)
    await _poll(updater, session, restricted_mode=0)
    await _confirm(updater, session)
    assert await updater.send_command("oneCharge", 1)
    assert _number(updater, "currentSet").native_max_value == 32


async def test_socket_restriction_survives_an_older_in_flight_readback(monkeypatch):
    updater, session, _ = _rig(monkeypatch)
    await _poll(updater, session, restricted_mode=0)
    await _confirm(updater, session)
    read_started = asyncio.Event()
    finish_read = asyncio.Event()
    original = common_network.fetch_json

    async def fetch(session_, url, **kwargs):
        if url.endswith("/main"):
            read_started.set()
            await finish_read.wait()
        return await original(session_, url, **kwargs)

    monkeypatch.setattr(common_network, "fetch_json", fetch)
    poll = asyncio.create_task(_poll(updater, session, restricted_mode=0))
    await read_started.wait()
    try:
        assert await updater.send_command("restricted_mode", 1)
    finally:
        finish_read.set()
        await poll
    number = _number(updater, "currentSet")
    assert number.native_max_value == 16
    await _poll(updater, session, restricted_mode=0)
    assert number.native_max_value == 32


async def test_queued_energy_limit_is_not_sent_in_the_wrong_units(monkeypatch):
    """Firmware identified while the write waits: the R3 encoding must not go out."""
    updater, session, _ = _rig(monkeypatch)
    await _poll(updater, session, energyLimit=10)
    entity = _number(updater, "energyLimit")
    session.calls.clear()
    async with updater._command_manager._lock.hold(1):
        task = asyncio.create_task(entity.async_set_native_value(10))
        await asyncio.sleep(0)
        assert not task.done()
        await _confirm(updater, session)
    with pytest.raises(HomeAssistantError) as err:
        await task
    assert "= 10" in str(err.value)
    assert str(err.value).endswith(
        "not sent: the charger's firmware or maximum changed while it waited. Try again."
    )
    assert not any(call.get("data") == "energyLimit=10000" for call in session.calls)
    assert not any(call["url"].endswith("/pageEvent") for call in session.calls)


async def test_energy_limit_written_after_identification_uses_kwh(monkeypatch):
    updater, session, _ = _rig(monkeypatch)
    await _poll(updater, session, energyLimit=10)
    await _confirm(updater, session)
    entity = _number(updater, "energyLimit")
    await entity.async_set_native_value(10)
    assert session.calls[-1]["data"] == "energyLimit=10"


@pytest.mark.parametrize(("key", "wire"), _FRACTIONAL_LIMITS)
async def test_fractional_limit_publishes_step_once_on_legacy_identification(monkeypatch, key, wire):
    updater, session, _ = _rig(monkeypatch)
    await _poll(updater, session, **{key: 10.5})
    entity = _number(updater, key)
    assert entity.native_step == 1
    updater.update_interval = None
    updater.async_add_listener(entity._handle_coordinator_update)
    updater.async_update_listeners()
    entity.async_write_ha_state.reset_mock()

    await _confirm(updater, session)
    assert entity.native_step == 0.1
    assert entity.native_value == 10.5
    await _poll(updater, session, **{wire: 10.5})
    updater.async_update_listeners()
    entity.async_write_ha_state.assert_called_once_with()
    await entity.async_set_native_value(10.5)
    assert session.calls[-1]["data"] == f"{wire}=10.5"


@pytest.mark.parametrize("filename", ["real_main_response.json", "reve_main_response.json"])
async def test_nonlegacy_limit_steps_match_main(monkeypatch, filename):
    updater, session, _ = _rig(monkeypatch)
    await _poll(updater, session, **_main_fixture(filename))
    assert {
        key: _number(updater, key).native_step
        for key in ("timeLimit", "energyLimit", *(key for key, _ in _FRACTIONAL_LIMITS))
    } == {"timeLimit": 5, "energyLimit": 1, "moneyLimit": 1, "sh1EnergyValue": 1, "sh2EnergyValue": 1}


@pytest.mark.parametrize(("key", "wire"), _CURRENTS)
async def test_queued_current_above_a_new_16_a_cap_is_not_sent(monkeypatch, key, wire):
    updater, session, _ = _rig(monkeypatch)
    await _poll(updater, session, restricted_mode=0)
    await _confirm(updater, session)
    entity = _number(updater, key)
    session.calls.clear()
    async with updater._command_manager._lock.hold(1):
        task = asyncio.create_task(entity.async_set_native_value(25))
        await asyncio.sleep(0)
        await _poll(updater, session, restricted_mode=1)
        assert entity.native_max_value == 16
    with pytest.raises(HomeAssistantError, match="not sent"):
        await task
    assert not any(call.get("data") == f"{wire}=25" for call in session.calls)


@pytest.mark.parametrize(("key", "wire"), _CURRENTS)
async def test_queued_current_within_the_cap_is_still_sent(monkeypatch, key, wire):
    updater, session, _ = _rig(monkeypatch)
    await _poll(updater, session, restricted_mode=0)
    await _confirm(updater, session)
    entity = _number(updater, key)
    async with updater._command_manager._lock.hold(1):
        task = asyncio.create_task(entity.async_set_native_value(12))
        await asyncio.sleep(0)
        await _poll(updater, session, restricted_mode=1)
    await task
    assert session.calls[-1]["data"] == f"{wire}=12"


@pytest.mark.parametrize(("key", "wire"), _CURRENTS)
async def test_16_a_adapter_caps_the_current_controls(monkeypatch, key, wire):
    updater, session, _ = _rig(monkeypatch)
    await _poll(updater, session, curDesign=32, adapter=16, restricted_mode=0)
    await _confirm(updater, session)
    entity = _number(updater, key)
    assert entity.native_max_value == 16
    await entity.async_set_native_value(25)
    assert session.calls[-1]["data"] == f"{wire}=16"


@pytest.mark.parametrize("adapter", [255, 32, None])
async def test_other_adapter_codes_keep_the_full_current(monkeypatch, adapter):
    updater, session, _ = _rig(monkeypatch)
    extra = {} if adapter is None else {"adapter": adapter}
    await _poll(updater, session, curDesign=32, restricted_mode=0, **extra)
    await _confirm(updater, session)
    assert _number(updater, "currentSet").native_max_value == 32


async def test_16_a_adapter_is_ignored_before_identification(monkeypatch):
    updater, session, _ = _rig(monkeypatch)
    await _poll(updater, session, curDesign=32, adapter=16, restricted_mode=0)
    assert _number(updater, "currentSet").native_max_value == 32


async def test_identification_maps_the_reply_it_follows(monkeypatch):
    """A 1.x pilot fault (4) read before /init must not stay published as Charging."""
    updater, session, _ = _rig(monkeypatch)
    fields = {"state": 4, "pilot": 4, "powerMeas": 0}
    await _poll(updater, session, **fields)
    await _confirm(updater, session)
    assert updater.snapshot.state == 7
    assert updater.snapshot.session_active is None
    assert updater.data["subState"] == 7  # Pilot Error
    assert updater.data[LEGACY_RAW_STATE_KEY] == 4
    await _poll(updater, session, **fields)
    assert updater.snapshot.state == 7
    assert updater.snapshot.informational == frozenset()


async def test_identification_keeps_an_already_translated_code(monkeypatch):
    updater, session, _ = _rig(monkeypatch)
    await _poll(updater, session, state=20, pilot=2)
    raw = dict(updater.data)
    await _confirm(updater, session)
    assert updater.data["state"] == raw["state"]
    assert updater.data["subState"] == raw["subState"]


async def test_no_disconnect_event_for_a_charger_empty_from_the_start(monkeypatch):
    updater, session, bus = _rig(monkeypatch)
    await _poll(updater, session, state=18, pilot=4, limitsStatus=8)
    await _confirm(updater, session)
    await _poll(updater, session, state=18, pilot=4, limitsStatus=8)
    await _poll(updater, session, state=18, pilot=1, limitsStatus=8)
    assert updater.snapshot.plugged_in is False
    assert "eveus_car_disconnected" not in [event for event, _ in bus.fired]


async def test_car_leaving_during_a_hold_still_fires_disconnect(monkeypatch):
    updater, session, bus = _rig(monkeypatch)
    await _poll(updater, session, state=2, pilot=2)
    await _confirm(updater, session)
    await _poll(updater, session, state=2, pilot=2)
    await _poll(updater, session, state=18, pilot=4, limitsStatus=8)
    assert "eveus_car_disconnected" not in [event for event, _ in bus.fired]
    await _poll(updater, session, state=18, pilot=1, limitsStatus=8)
    assert [event for event, _ in bus.fired].count("eveus_car_disconnected") == 1


async def test_identification_forgets_a_presence_read_in_the_r3_meaning(monkeypatch):
    """Raw 2 read before /init is Standby in R3 terms; on 1.x it is a car waiting."""
    updater, session, bus = _rig(monkeypatch)
    await _poll(updater, session, state=2, pilot=2)
    await _confirm(updater, session)
    await _poll(updater, session, state=2, pilot=2)
    assert "eveus_car_connected" not in [event for event, _ in bus.fired]


async def test_no_plug_event_across_a_gap_with_an_unknown_pilot(monkeypatch):
    updater, session, bus = _rig(monkeypatch)
    await _poll(updater, session, state=2, pilot=2)
    await _confirm(updater, session)
    await _poll(updater, session, state=2, pilot=2)
    updater._forget_poll_gap_state()
    await _poll(updater, session, state=18, pilot=4, limitsStatus=8)
    await _poll(updater, session, state=18, pilot=4, limitsStatus=8)
    await _poll(updater, session, state=2, pilot=2)
    events = [event for event, _ in bus.fired]
    assert "eveus_car_connected" not in events and "eveus_car_disconnected" not in events


async def test_car_arriving_after_an_unknown_pilot_fires_connect(monkeypatch):
    updater, session, bus = _rig(monkeypatch)
    await _poll(updater, session, state=1, pilot=1)
    await _confirm(updater, session)
    await _poll(updater, session, state=1, pilot=1)
    await _poll(updater, session, state=18, pilot=4, limitsStatus=8)
    await _poll(updater, session, state=18, pilot=2, limitsStatus=8)
    assert [event for event, _ in bus.fired].count("eveus_car_connected") == 1


@pytest.mark.parametrize("outage", [False, True])
async def test_definite_legacy_plug_transition_with_same_canonical_state(monkeypatch, outage):
    updater, session, bus = _rig(monkeypatch)
    await _poll(updater, session, state=1, pilot=1)
    await _confirm(updater, session)
    await _poll(updater, session, state=1, pilot=1)
    assert updater.snapshot.plugged_in is False
    if outage:
        updater._forget_poll_gap_state()
    await _poll(updater, session, state=3, pilot=4)
    assert updater.snapshot.plugged_in is None
    assert not bus.fired
    canonical = updater.snapshot.state
    await _poll(updater, session, state=3, pilot=2)
    assert updater.snapshot.state == canonical
    assert updater.snapshot.plugged_in is True
    await _poll(updater, session, state=3, pilot=2)
    events = [event for event, _ in bus.fired]
    assert events == ([] if outage else ["eveus_car_connected"])


async def test_classic_r3_plug_events_still_follow_state_and_keep_order(monkeypatch):
    updater, session, bus = _rig(monkeypatch)
    data = _main_fixture()
    await _poll(updater, session, **(data | {"state": 2, "pilot": 1}))
    await _poll(updater, session, **(data | {"state": 4, "pilot": 4}))
    assert updater.snapshot.plugged_in is True
    await _poll(updater, session, **(data | {"state": 4, "pilot": 2}))
    assert [event for event, _ in bus.fired] == [
        "eveus_charging_started", "eveus_car_connected",
    ]


def _init_reply(monkeypatch, value):
    original = common_network.fetch_json

    async def fetch(session, url, **kwargs):
        if url.endswith("/init"):
            return {"ESP_SW_version": 155, "minVoltage": value}
        return await original(session, url, **kwargs)

    monkeypatch.setattr(common_network, "fetch_json", fetch)


async def test_minimum_voltage_changed_on_the_charger_page_is_read_back(monkeypatch):
    updater, session, _ = _rig(monkeypatch)
    await _poll(updater, session)
    await _confirm(updater, session, minVoltage=175)
    select = EveusMinVoltageSelect(updater)
    _init_reply(monkeypatch, 180)
    session.calls.clear()
    await _poll(updater, session)
    assert select.current_option == "175"  # not re-read on every poll
    clock = updater._legacy_init_read_at + common_network._LEGACY_INIT_REFRESH_SECONDS - 0.5
    monkeypatch.setattr(common_network.time, "monotonic", lambda: clock)
    await _poll(updater, session)
    assert select.current_option == "175"
    clock = updater._legacy_init_read_at + common_network._LEGACY_INIT_REFRESH_SECONDS
    monkeypatch.setattr(common_network.time, "monotonic", lambda: clock)
    await _poll(updater, session)
    assert select.current_option == "180"
    await _poll(updater, session)
    assert select.current_option == "180"


@pytest.mark.parametrize("filename", ["real_main_response.json", "reve_main_response.json"])
async def test_modern_firmware_never_rereads_init(monkeypatch, filename):
    updater, session, _ = _rig(monkeypatch)
    data = _main_fixture(filename)
    await _poll(updater, session, **data)
    clock = time.monotonic() + 10 * common_network._LEGACY_INIT_REFRESH_SECONDS
    monkeypatch.setattr(common_network.time, "monotonic", lambda: clock)
    fetched = []
    original = common_network.fetch_json

    async def fetch(session_, url, **kwargs):
        fetched.append(url)
        return await original(session_, url, **kwargs)

    monkeypatch.setattr(common_network, "fetch_json", fetch)
    await _poll(updater, session, **data)
    assert not any(url.endswith("/init") for url in fetched)


@pytest.mark.parametrize("source", ["wifi", "remembered-r3", "reve"])
async def test_init_retry_stops_after_positive_nonlegacy_identification(monkeypatch, source):
    updater, session, _ = _rig(monkeypatch)
    clock = 1000.0
    monkeypatch.setattr(common_network, "time", SimpleNamespace(
        monotonic=lambda: clock, time=time.time,
    ))
    monkeypatch.setattr(
        updater.hass, "async_create_background_task",
        lambda coro, name: asyncio.create_task(coro), raising=False,
    )
    original = common_network.fetch_json
    attempts = []

    async def fetch(session_, url, **kwargs):
        if url.endswith("/init"):
            attempts.append(clock)
            raise TimeoutError
        return await original(session_, url, **kwargs)

    monkeypatch.setattr(common_network, "fetch_json", fetch)
    await _poll(updater, session)
    await updater.async_maybe_fetch_init_firmware()
    assert attempts == [1000.0]

    data = _main_fixture("reve_main_response.json" if source == "reve" else "real_main_response.json")
    if source == "remembered-r3":
        await _poll(updater, session, **data)
        del data["verFWWifi"]
    del data["verFWMain"]
    assert "firmware" not in data
    for _ in range(2):
        clock += common_network._INIT_RETRY_SECONDS
        await _poll(updater, session, **data)
        if updater._init_fw_task is not None:
            await updater._init_fw_task
    assert attempts == [1000.0]


@pytest.mark.parametrize("reply", [
    {}, [], {"ESP_SW_version": "155"}, {"ESP_SW_version": True},
    {"ESP_SW_version": -1}, {"ESP_SW_version": 10**6 + 1}, 404, 503,
])
async def test_unusable_legacy_init_refresh_keeps_profile_and_backs_off(monkeypatch, reply):
    updater, session, _ = _rig(monkeypatch)
    clock = 1000.0
    monkeypatch.setattr(common_network, "time", SimpleNamespace(
        monotonic=lambda: clock, time=time.time,
    ))
    await _poll(updater, session)
    await _confirm(updater, session, minVoltage=175)
    original = common_network.fetch_json
    attempts = []

    async def fetch(session_, url, **kwargs):
        if url.endswith("/init"):
            attempts.append(clock)
            if isinstance(reply, int):
                raise common_network.aiohttp.ClientResponseError(
                    request_info=None, history=(), status=reply,
                )
            return reply
        return await original(session_, url, **kwargs)

    monkeypatch.setattr(common_network, "fetch_json", fetch)
    clock += common_network._LEGACY_INIT_REFRESH_SECONDS
    first_refresh = clock
    await _poll(updater, session)
    await _poll(updater, session)
    clock += common_network._INIT_RETRY_SECONDS - 1
    await _poll(updater, session)
    assert attempts == [first_refresh]
    assert updater.is_legacy_firmware
    assert updater._init_fw_fallback == "1.55"
    assert updater.snapshot.get_int("minVoltage") == 175

    reply = {"ESP_SW_version": 151, "minVoltage": 180}
    clock += 1
    await _poll(updater, session)
    await _poll(updater, session)
    assert attempts == [first_refresh, clock]
    assert updater._init_fw_fallback == "1.51"
    assert updater.snapshot.get_int("minVoltage") == 180
