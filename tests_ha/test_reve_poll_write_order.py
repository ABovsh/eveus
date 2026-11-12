"""Audit 2026-11-12 #12 through Home Assistant services: an older poll must
not make One Charge off release a Stop that was just applied."""
import asyncio
from copy import deepcopy
import json

import pytest

from custom_components.eveus.reve import MEASUREMENTS_QUERY, MEASUREMENTS_AND_SETTINGS_QUERY
from .conftest import _entry, _setup
from .test_reve_smoke import HOST, REVE_MAIN, REVE_GRAPHQL

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.mark.parametrize("old_reply_arrives_last", [True, False], ids=["race", "ordered-control"])
async def test_one_charge_off_does_not_release_a_stop_after_an_older_poll(
    hass, aioclient_mock, monkeypatch, old_reply_arrives_last
):
    response = deepcopy(REVE_GRAPHQL)
    response["data"]["stationData"].update(stationState=4, chargingCommand=0)
    response["data"]["updateStationData"] = True
    aioclient_mock.post(f"http://{HOST}/main", json=REVE_MAIN)
    aioclient_mock.post(f"http://{HOST}/graphql", json=response)
    aioclient_mock.post(f"http://{HOST}/pageEvent", text="ok")
    entry = _entry(hass, HOST)
    await _setup(hass, entry)
    updater = entry.runtime_data.updater
    # The outstanding old poll is the only one during the controlled sequence.
    monkeypatch.setattr(updater, "_schedule_post_command_refresh", lambda: None)
    api = updater._reve
    original = api._station
    entered = asyncio.Event()
    release = asyncio.Event()

    async def delayed(document):
        reply = deepcopy(await original(document))
        if document in (MEASUREMENTS_QUERY, MEASUREMENTS_AND_SETTINGS_QUERY):
            entered.set()
            await release.wait()
        return reply

    monkeypatch.setattr(api, "_station", delayed)
    poll = asyncio.create_task(updater.async_refresh())
    await asyncio.wait_for(entered.wait(), 2)
    if not old_reply_arrives_last:
        release.set()
        await asyncio.wait_for(poll, 2)
    await hass.services.async_call("switch", "turn_on", {
        "entity_id": "switch.eveus_ev_charger_stop_charging",
    }, blocking=True)
    assert api._extras.charging_command == 2
    release.set()
    await asyncio.wait_for(poll, 2)
    await hass.services.async_call("switch", "turn_off", {
        "entity_id": "switch.eveus_ev_charger_one_charge",
    }, blocking=True)
    changes = []
    for method, url, body, headers in aioclient_mock.mock_calls:
        if str(url).endswith("/graphql"):
            payload = json.loads(body)
            if payload["operationName"] == "UpdateStationData":
                changes.append(payload["variables"]["input"])
    assert changes == [{"chargingCommand": 2}], f"Stop was followed by an unwanted START: {changes}"
