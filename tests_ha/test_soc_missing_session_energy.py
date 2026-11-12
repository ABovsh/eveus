"""Audit 2026-11-12 #11: a missing session-energy reading is zero only when the
car is known to be unplugged; inside a plug-in cycle the SOC estimate is
unavailable instead of falling back to Initial SOC."""
from copy import deepcopy

import pytest

from custom_components.eveus.const import (
    CONF_SOC_MODE, SOC_MODE_ADVANCED, CONF_INITIAL_SOC,
    CONF_BATTERY_CAPACITY, CONF_SOC_CORRECTION, CONF_TARGET_SOC,
)
from custom_components.eveus.reve import SESSION_HOLD_SECONDS

from .conftest import _entry, _setup
from .test_reve_smoke import HOST, REVE_GRAPHQL, REVE_MAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.mark.parametrize("finished_state", [2, 3, 4, 5, 6, 7], ids=[
    "unplugged-control", "connected", "charging-control", "finished", "paused-control", "fault"
])
async def test_missing_finished_energy_must_not_publish_initial_soc_as_current(
    hass, aioclient_mock, finished_state
):
    main = {**REVE_MAIN, "state": finished_state}
    response = deepcopy(REVE_GRAPHQL)
    station = response["data"]["stationData"]
    station["stationState"] = finished_state
    station["session"] = {**station["session"], "sessionEnergy": 40, "sessionTime": 36000}
    aioclient_mock.post(f"http://{HOST}/main", json=main)
    aioclient_mock.post(f"http://{HOST}/graphql", json=response)
    aioclient_mock.post(f"http://{HOST}/pageEvent", text="ok")
    entry = _entry(hass, HOST)
    hass.config_entries.async_update_entry(entry, data={
        **entry.data, CONF_SOC_MODE: SOC_MODE_ADVANCED, CONF_INITIAL_SOC: 20,
        CONF_BATTERY_CAPACITY: 50, CONF_SOC_CORRECTION: 0, CONF_TARGET_SOC: 100,
    })
    await _setup(hass, entry)
    entity_id = "sensor.eveus_ev_charger_soc_percent"
    assert float(hass.states.get(entity_id).state) == 100
    updater = entry.runtime_data.updater
    # Advance only the session cache's age, leaving HA's scheduler untouched.
    session, captured = updater._reve._session
    updater._reve._session = (session, captured - SESSION_HOLD_SECONDS - 1)
    aioclient_mock.clear_requests()
    aioclient_mock.post(f"http://{HOST}/main", json=main)
    aioclient_mock.post(f"http://{HOST}/graphql", status=500)
    aioclient_mock.post(f"http://{HOST}/pageEvent", text="ok")
    await updater.async_refresh()
    assert updater.last_update_success
    assert not updater.snapshot.has("sessionEnergy")
    shown_without_energy = hass.states.get(entity_id).state
    # A fresh zero is real and still allowed; recovery restores the 100% result.
    aioclient_mock.clear_requests()
    recovered = deepcopy(response)
    recovered["data"]["stationData"]["session"]["sessionEnergy"] = 0
    aioclient_mock.post(f"http://{HOST}/main", json=main)
    aioclient_mock.post(f"http://{HOST}/graphql", json=recovered)
    updater._reve._measurements_retry_at = 0
    await updater.async_refresh()
    assert float(hass.states.get(entity_id).state) == 20
    aioclient_mock.clear_requests()
    aioclient_mock.post(f"http://{HOST}/main", json=main)
    aioclient_mock.post(f"http://{HOST}/graphql", json=response)
    await updater.async_refresh()
    assert float(hass.states.get(entity_id).state) == 100
    if finished_state == 2:
        assert float(shown_without_energy) == 20
        return
    assert shown_without_energy in ("unknown", "unavailable"), (
        "Missing session energy cannot prove zero delivered in this same plug-in cycle; "
        f"published {shown_without_energy}% instead"
    )
