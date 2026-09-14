"""Real-Home-Assistant lifecycle tests with only the charger's HTTP mocked.

`test_setup_smoke.py` proves an entry loads; these pin what a count cannot:
the exact entities each platform publishes, that a service call really reaches
the charger, and that unload, re-authentication, availability grace, reload
and a second charger all behave through Home Assistant's own machinery.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.eveus import common_base
from custom_components.eveus.const import (
    AVAILABILITY_GRACE_PERIOD,
    CONF_MODEL,
    CONF_PHASES,
    CONF_SCHEME,
    CONF_SOC_MODE,
    DOMAIN,
    MODEL_16A,
    SOC_MODE_BASIC,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

HOST_A = "192.168.1.50"  # NOSONAR(python:S1313) - RFC 1918 test fixture
HOST_B = "192.168.1.51"  # NOSONAR(python:S1313) - RFC 1918 test fixture

REAL_MAIN = json.loads(
    (Path(__file__).parent.parent / "tests" / "fixtures" / "real_main_response.json")
    .read_text(encoding="utf-8")
)

# One representative entity per platform, with the state the verbatim live
# capture must produce. A platform that silently fails to forward, or a getter
# that stops reading its field, changes one of these.
REPRESENTATIVE_STATES = {
    "sensor.eveus_ev_charger_state": "Standby",
    "sensor.eveus_ev_charger_voltage": "226.0",
    "binary_sensor.eveus_ev_charger_car_connected": "off",
    "switch.eveus_ev_charger_stop_charging": "off",
    "number.eveus_ev_charger_charging_current": "16.0",
    "select.eveus_ev_charger_adaptive_mode": "Voltage",
    "time.eveus_ev_charger_schedule_1_start": "23:00:00",
    "button.eveus_ev_charger_force_refresh": "unknown",
}
CURRENT = "number.eveus_ev_charger_charging_current"
VOLTAGE = "sensor.eveus_ev_charger_voltage"


def _entry(hass: HomeAssistant, host: str) -> MockConfigEntry:
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=host,
        data={
            CONF_HOST: host,
            CONF_USERNAME: "test_user",  # NOSONAR(python:S2068) - test fixture
            CONF_PASSWORD: "test_password",  # NOSONAR(python:S2068) - test fixture
            CONF_MODEL: MODEL_16A,
            CONF_SCHEME: "http",
            CONF_PHASES: 1,
            CONF_SOC_MODE: SOC_MODE_BASIC,
        },
        options={},
    )
    config_entry.add_to_hass(hass)
    return config_entry


def _mock_charger(aioclient_mock: AiohttpClientMocker, host: str, **main: object) -> None:
    aioclient_mock.post(f"http://{host}/main", **(main or {"json": REAL_MAIN}))
    aioclient_mock.post(f"http://{host}/pageEvent", text="ok")


def _calls(aioclient_mock: AiohttpClientMocker, host: str, path: str) -> list:
    return [
        call
        for call in aioclient_mock.mock_calls
        if call[1].host == host and call[1].path == path
    ]


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def _set_current(hass: HomeAssistant, entity_id: str, value: float) -> None:
    await hass.services.async_call(
        "number", "set_value", {"entity_id": entity_id, "value": value}, blocking=True
    )


async def test_every_platform_publishes_its_representative_entity(
    hass, aioclient_mock
) -> None:
    entry = _entry(hass, HOST_A)
    _mock_charger(aioclient_mock, HOST_A)
    await _setup(hass, entry)

    observed = {
        entity_id: getattr(hass.states.get(entity_id), "state", None)
        for entity_id in REPRESENTATIVE_STATES
    }
    assert observed == REPRESENTATIVE_STATES


async def test_service_call_reaches_the_charger_transport(hass, aioclient_mock) -> None:
    entry = _entry(hass, HOST_A)
    _mock_charger(aioclient_mock, HOST_A)
    await _setup(hass, entry)

    await _set_current(hass, CURRENT, 12)

    posts = _calls(aioclient_mock, HOST_A, "/pageEvent")
    assert len(posts) == 1
    assert parse_qs(posts[0][2]) == {"pageevent": ["currentSet"], "currentSet": ["12"]}


async def test_unload_during_command_leaves_no_later_charger_traffic(
    hass, aioclient_mock
) -> None:
    entry = _entry(hass, HOST_A)
    aioclient_mock.post(f"http://{HOST_A}/main", json=REAL_MAIN)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_command(_method, _url, _data):
        entered.set()
        await release.wait()
        return AiohttpClientMockResponse("post", _url, text="ok")

    aioclient_mock.post(f"http://{HOST_A}/pageEvent", side_effect=blocked_command)
    await _setup(hass, entry)
    updater = entry.runtime_data.updater

    command = hass.async_create_task(_set_current(hass, CURRENT, 10))
    await asyncio.wait_for(entered.wait(), 5)
    assert await hass.config_entries.async_unload(entry.entry_id)
    release.set()
    await asyncio.gather(command, return_exceptions=True)
    await hass.async_block_till_done()
    calls_at_unload = len(aioclient_mock.mock_calls)

    # Any post-command refresh burst or poll of the old instance would land
    # within these windows.
    for seconds in (5, 15, 30, 120):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert updater._shutting_down is True
    assert len(aioclient_mock.mock_calls) == calls_at_unload


async def test_runtime_401_starts_reauth_without_reloading(hass, aioclient_mock) -> None:
    entry = _entry(hass, HOST_A)
    _mock_charger(aioclient_mock, HOST_A)
    await _setup(hass, entry)
    updater = entry.runtime_data.updater

    aioclient_mock.clear_requests()
    aioclient_mock.post(f"http://{HOST_A}/main", status=401)
    await updater.async_refresh()
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.updater is updater


async def test_failed_polls_hold_through_grace_then_expire_and_recover(
    hass, aioclient_mock, monkeypatch
) -> None:
    clock = {"now": time.monotonic()}
    monkeypatch.setattr(
        common_base,
        "time",
        SimpleNamespace(monotonic=lambda: clock["now"], time=time.time),
    )
    entry = _entry(hass, HOST_A)
    _mock_charger(aioclient_mock, HOST_A)
    await _setup(hass, entry)
    updater = entry.runtime_data.updater

    async def poll() -> str:
        await updater.async_refresh()
        await hass.async_block_till_done()
        return hass.states.get(VOLTAGE).state

    aioclient_mock.clear_requests()
    aioclient_mock.post(f"http://{HOST_A}/main", exc=asyncio.TimeoutError())
    assert await poll() == "226.0"

    # Home Assistant does not re-notify listeners on a repeated failure, so
    # expiry must come from the entity's own grace recheck timer.
    clock["now"] += AVAILABILITY_GRACE_PERIOD - 1
    assert await poll() == "226.0"

    clock["now"] += 2
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=AVAILABILITY_GRACE_PERIOD + 1)
    )
    await hass.async_block_till_done()
    assert hass.states.get(VOLTAGE).state == STATE_UNAVAILABLE

    aioclient_mock.clear_requests()
    _mock_charger(aioclient_mock, HOST_A)
    updater._next_poll_attempt = 0  # skip the offline backoff wait, not the recovery
    assert await poll() == "226.0"


async def test_reload_replaces_the_instance_and_stops_the_old_one(
    hass, aioclient_mock
) -> None:
    entry = _entry(hass, HOST_A)
    _mock_charger(aioclient_mock, HOST_A)
    await _setup(hass, entry)
    old_updater = entry.runtime_data.updater

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.updater is not old_updater
    assert old_updater._shutting_down is True
    assert hass.states.get(CURRENT).state == "16.0"
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


async def test_two_chargers_stay_isolated(hass, aioclient_mock) -> None:
    entry_a = _entry(hass, HOST_A)
    entry_b = _entry(hass, HOST_B)
    _mock_charger(aioclient_mock, HOST_A)
    _mock_charger(aioclient_mock, HOST_B)
    # Setting up the domain loads every entry for it.
    await _setup(hass, entry_a)
    assert entry_b.state is ConfigEntryState.LOADED

    registry = er.async_get(hass)
    current_b = next(
        item.entity_id
        for item in er.async_entries_for_config_entry(registry, entry_b.entry_id)
        if item.unique_id.endswith("charging_current")
        or item.entity_id.endswith("charging_current")
    )
    assert current_b != CURRENT

    await _set_current(hass, current_b, 9)
    assert _calls(aioclient_mock, HOST_A, "/pageEvent") == []
    assert len(_calls(aioclient_mock, HOST_B, "/pageEvent")) == 1

    assert await hass.config_entries.async_unload(entry_a.entry_id)
    await hass.async_block_till_done()
    assert entry_b.state is ConfigEntryState.LOADED
    assert hass.states.get(current_b).state != STATE_UNAVAILABLE
