"""Pre-R3 (1.x/2.x) corner cases: queued writes, confirmation and plug events."""

import asyncio
import time
from unittest.mock import Mock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.eveus import common_network
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


async def test_modern_firmware_never_rereads_init(monkeypatch):
    updater, session, _ = _rig(monkeypatch)
    await _poll(updater, session, verFWMain="GRM070A-R3.05.7 ")
    clock = time.monotonic() + 10 * common_network._LEGACY_INIT_REFRESH_SECONDS
    monkeypatch.setattr(common_network.time, "monotonic", lambda: clock)
    fetched = []
    original = common_network.fetch_json

    async def fetch(session_, url, **kwargs):
        fetched.append(url)
        return await original(session_, url, **kwargs)

    monkeypatch.setattr(common_network, "fetch_json", fetch)
    await _poll(updater, session, verFWMain="GRM070A-R3.05.7 ")
    assert not any(url.endswith("/init") for url in fetched)
