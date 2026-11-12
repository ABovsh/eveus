"""A REVE charger set up in a real Home Assistant, end to end.

The unit suite covers the REVE normalisation and command routing in detail;
this proves the whole path — setup, the first poll with its GraphQL read, the
entity states Home Assistant publishes and the Repairs it raises — on a real
Home Assistant, against the recorded REVE ``/main`` and GraphQL replies.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.eveus.const import DOMAIN

from .conftest import _entry, _setup

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

HOST = "192.168.1.62"  # NOSONAR(python:S1313) - RFC 1918 test fixture
_FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"
REVE_MAIN = json.loads((_FIXTURES / "reve_main_response.json").read_text(encoding="utf-8"))
REVE_EXTRAS = json.loads((_FIXTURES / "reve_graphql_extras.json").read_text(encoding="utf-8"))
REVE_STATION = json.loads((_FIXTURES / "reve_graphql_station.json").read_text(encoding="utf-8"))
# One reply for every read document: the settings and the measurements
# captured live (the mock cannot answer by request body; extra fields are
# harmless to every reader).
REVE_GRAPHQL = {"data": {"stationData": {
    **REVE_STATION["data"]["stationData"], **REVE_EXTRAS["data"]["stationData"],
}}}

_PREFIX = "eveus_ev_charger"


async def _setup_reve(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(f"http://{HOST}/main", json=REVE_MAIN)
    aioclient_mock.post(f"http://{HOST}/graphql", json=REVE_GRAPHQL)
    aioclient_mock.post(f"http://{HOST}/pageEvent", text="ok")
    await _setup(hass, _entry(hass, HOST))


async def test_reve_charger_publishes_its_real_settings(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    await _setup_reve(hass, aioclient_mock)

    def state(entity_id: str) -> str:
        return hass.states.get(entity_id).state

    for entity_id in (
        f"sensor.{_PREFIX}_leakage_current",
        f"sensor.{_PREFIX}_leakage_current_peak",
        f"sensor.{_PREFIX}_plug_temperature",
        f"binary_sensor.{_PREFIX}_ocpp_connected",
    ):
        assert state(entity_id) == STATE_UNAVAILABLE, entity_id
    # REVE has no schedule energy limit and resets "disable all limits": not built.
    for entity_id in (
        f"number.{_PREFIX}_schedule_1_energy_limit",
        f"number.{_PREFIX}_schedule_2_energy_limit",
        f"switch.{_PREFIX}_schedule_1_energy_limit_enabled",
        f"switch.{_PREFIX}_schedule_2_energy_limit_enabled",
        f"switch.{_PREFIX}_limit_disable_all",
    ):
        assert hass.states.get(entity_id) is None, entity_id
    assert hass.config_entries.async_entries(DOMAIN)[0].data["firmware_family"] == "reve"

    assert state(f"switch.{_PREFIX}_schedule_1_enabled") == "off"
    assert state(f"switch.{_PREFIX}_stop_charging") == "off"
    assert state(f"binary_sensor.{_PREFIX}_reve_api") == "on"
    assert state(f"time.{_PREFIX}_schedule_1_start") == "23:00:00"
    assert state(f"time.{_PREFIX}_schedule_2_stop") == "18:00:00"
    assert float(state(f"sensor.{_PREFIX}_primary_rate_cost")) == 0
    # Every recorded rate is disabled: the base price is the active one.
    assert float(state(f"sensor.{_PREFIX}_active_rate_cost")) == 0
    assert state(f"sensor.{_PREFIX}_rate_2_status") == "Disabled"
    assert float(state(f"number.{_PREFIX}_charging_current")) == 7
    assert state(f"sensor.{_PREFIX}_state") == "Standby"
    graphql_calls = [call for call in aioclient_mock.mock_calls if str(call[1]).endswith("/graphql")]
    # One read right after /main recognised the firmware: the measurements
    # with the schedules and rates.
    # The Last Session voltages are one more read, once, shared by both sensors.
    sessions = [c for c in graphql_calls if json.loads(c[2])["operationName"] == "ListSessions"]
    assert len(sessions) == 1
    graphql_calls = [c for c in graphql_calls if c not in sessions]
    assert len(graphql_calls) == 1
    query = json.loads(graphql_calls[0][2])["query"]
    assert query.count("stationState") == 1
    assert "scheduleV2" in query and "ratesV2" in query


async def test_reve_raw_leak_counts_raise_no_repair(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    await _setup_reve(hass, aioclient_mock)
    # The leakage notice needs consecutive polls above the threshold.
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    for _ in range(3):
        await entry.runtime_data.updater.async_refresh()
        await hass.async_block_till_done()
    issues = [
        issue_id
        for (domain, issue_id) in ir.async_get(hass).issues
        if domain == DOMAIN and "leakage" in issue_id
    ]
    assert issues == []


async def test_last_session_voltage_retries_do_not_block_startup(hass) -> None:
    """Audit 2026-11-12 #03: the optional station read is background work."""
    import asyncio
    from unittest.mock import AsyncMock

    from custom_components.eveus.common_network import EveusUpdater
    from custom_components.eveus.const import MODEL_16A
    from custom_components.eveus.reve_sensors import create_reve_last_session_sensors
    from tests_ha.conftest import _entry

    updater = EveusUpdater(host=HOST, username="", password="", hass=hass,
                           config_entry=_entry(hass, HOST), device_number=1, model=MODEL_16A)
    sensor = create_reve_last_session_sensors(updater)[0]
    sensor.hass = hass
    sensor.async_refresh_from_station = AsyncMock(return_value=False)
    sensor._start_reads((0.0, 30.0, 60.0), None)
    task = sensor._read_task
    try:
        await asyncio.sleep(0)
        assert task not in hass._tasks
        await hass.async_block_till_done()
        assert not task.done(), "startup work must not wait for the retry sleeps"
    finally:
        sensor._cancel_reads()
        await asyncio.gather(task, return_exceptions=True)
