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
        f"number.{_PREFIX}_schedule_1_energy_limit",
        f"switch.{_PREFIX}_schedule_1_energy_limit_enabled",
        f"binary_sensor.{_PREFIX}_ocpp_connected",
    ):
        assert state(entity_id) == STATE_UNAVAILABLE, entity_id

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
    # The measurements, right after /main recognised the firmware, then one
    # settings read: the schedules, the rates and Stop, one small document each.
    assert len(graphql_calls) == 4
    assert json.loads(graphql_calls[0][2])["query"].count("stationState") == 1


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
