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
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_FINAL_WRITE,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.eveus import common_base, common_network
from custom_components import eveus
from custom_components.eveus.const import (
    AVAILABILITY_GRACE_PERIOD,
    CONF_BATTERY_CAPACITY,
    CONF_INITIAL_SOC,
    CONF_EXTERNAL_SOC_ENTITY,
    CONF_MODEL,
    CONF_PHASES,
    CONF_SCHEME,
    CONF_SOC_CORRECTION,
    CONF_SOC_MODE,
    CONF_TARGET_SOC,
    DOMAIN,
    MODEL_16A,
    SOC_MODE_ADVANCED,
)

from .conftest import _entry, _mock_charger, _setup

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


@pytest.mark.parametrize("readonly", [True, False])
async def test_schedule_weekdays_use_real_home_assistant_user_permissions(
    hass, hass_read_only_user, hass_admin_user, monkeypatch, readonly,
) -> None:
    """The custom write must enforce the same policy as an entity service."""
    entry = _entry(hass, HOST_A)
    registry = er.async_get(hass)
    entity = registry.async_get_or_create(
        "switch", DOMAIN, "eveus_schedule_2_enabled", config_entry=entry,
    )
    registry.async_update_entity(entity.entity_id, new_entity_id="switch.custom_schedule")
    updater = SimpleNamespace(
        device_number=1, firmware_family="reve", send_command=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(eveus, "_updater_for", lambda *_: updater)
    connection = MagicMock()
    connection.user = hass_read_only_user if readonly else hass_admin_user
    await eveus._async_reve_schedule_weekdays(hass, connection, {
        "id": 17, "type": "eveus/reve_schedule_weekdays", "schedule": 2, "weekdays": [1, 3],
    })
    if readonly:
        updater.send_command.assert_not_awaited()
        connection.send_error.assert_called_once_with(17, "unauthorized", "Unauthorized")
    else:
        updater.send_command.assert_awaited_once_with("sh2Weekdays", [1, 3])
        connection.send_result.assert_called_once_with(17, {"weekdays": [1, 3]})


def _calls(aioclient_mock: AiohttpClientMocker, host: str, path: str) -> list:
    return [
        call
        for call in aioclient_mock.mock_calls
        if call[1].host == host and call[1].path == path
    ]


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
    # The outage clock is the coordinator's (common_network).
    for module in (common_base, common_network):
        monkeypatch.setattr(
            module,
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


class _FakeLovelaceResources:
    """Minimal storage-mode Lovelace resource collection for the removal test."""

    def __init__(self, items):
        self.items = [dict(item) for item in items]

    def async_items(self):
        return list(self.items)

    async def async_delete_item(self, item_id):
        self.items = [item for item in self.items if item["id"] != item_id]


async def test_removing_the_last_entry_deletes_the_dashboard_card_resource(
    hass, aioclient_mock
) -> None:
    from custom_components.eveus import CARD_URL

    entry = _entry(hass, HOST_A)
    _mock_charger(aioclient_mock, HOST_A)
    await _setup(hass, entry)

    resources = _FakeLovelaceResources(
        [{"id": "card1", "res_type": "module", "url": f"{CARD_URL}?v=abc123"}]
    )
    hass.data["lovelace"] = SimpleNamespace(resources=resources, resource_mode="storage")

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert resources.items == []


async def test_disabled_soc_correction_entity_does_not_blank_soc_percent(
    hass, aioclient_mock
) -> None:
    """A disabled number entity never gets async_added_to_hass, so SOC
    Percent must come from a calculator seeded at setup time, not solely
    from the entity pushing its value."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=HOST_A,
        data={
            CONF_HOST: HOST_A,
            CONF_USERNAME: "test_user",  # NOSONAR(python:S2068) - test fixture
            CONF_PASSWORD: "test_password",  # NOSONAR(python:S2068) - test fixture
            CONF_MODEL: MODEL_16A,
            CONF_SCHEME: "http",
            CONF_PHASES: 1,
            CONF_SOC_MODE: SOC_MODE_ADVANCED,
            CONF_INITIAL_SOC: 50,
            CONF_TARGET_SOC: 80,
            CONF_BATTERY_CAPACITY: 60,
            CONF_SOC_CORRECTION: 5,
        },
        options={},
    )
    entry.add_to_hass(hass)
    _mock_charger(aioclient_mock, HOST_A)
    await _setup(hass, entry)

    registry = er.async_get(hass)
    correction_entity_id = next(
        item.entity_id
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
        if item.unique_id.endswith("soc_correction")
    )
    registry.async_update_entity(
        correction_entity_id, disabled_by=er.RegistryEntryDisabler.USER
    )

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    soc_percent_entity_id = next(
        item.entity_id
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
        if item.unique_id.endswith("soc_percent")
    )
    state = hass.states.get(soc_percent_entity_id)
    assert state is not None
    assert state.state not in (None, "unknown", "unavailable")
    float(state.state)


@pytest.mark.parametrize(("initial_state", "seeded", "car_soc"), [
    (2, True, 25), (2, True, 85), (4, False, 25), (4, False, 85),
])
async def test_car_soc_is_applied_before_limit_enforcement(
    hass, aioclient_mock, monkeypatch, initial_state, seeded, car_soc,
) -> None:
    from custom_components.eveus.number import EveusInitialSocNumber
    from custom_components.eveus.switch import EveusSocLimitSwitch

    entry = _entry(hass, HOST_A)
    hass.config_entries.async_update_entry(entry, data={
        **entry.data, CONF_SOC_MODE: SOC_MODE_ADVANCED,
        CONF_INITIAL_SOC: 85, CONF_TARGET_SOC: 80, CONF_BATTERY_CAPACITY: 60,
        CONF_SOC_CORRECTION: 0, CONF_EXTERNAL_SOC_ENTITY: "sensor.car_soc",
    })
    hass.states.async_set("sensor.car_soc", str(car_soc))
    monkeypatch.setattr(EveusInitialSocNumber, "async_get_last_extra_data", AsyncMock(
        return_value=SimpleNamespace(as_dict=lambda: {"seeded": seeded}),
    ))
    monkeypatch.setattr(EveusSocLimitSwitch, "async_get_last_state", AsyncMock(
        return_value=SimpleNamespace(state="on"),
    ))
    payload = {**REAL_MAIN, "state": initial_state, "evseEnabled": 0, "suspendLimits": 0,
               "sessionEnergy": 0, "sessionTime": 0}
    _mock_charger(aioclient_mock, HOST_A, json=payload)
    await _setup(hass, entry)
    if car_soc < 80:
        assert _calls(aioclient_mock, HOST_A, "/pageEvent") == []
    await hass.services.async_call("switch", "turn_on", {
        "entity_id": "switch.eveus_ev_charger_limit_soc_enabled",
    }, blocking=True)
    aioclient_mock.clear_requests()
    _mock_charger(aioclient_mock, HOST_A, json={**payload, "state": 4, "sessionTime": 30})
    await entry.runtime_data.updater.async_refresh()
    await hass.async_block_till_done()
    posts = _calls(aioclient_mock, HOST_A, "/pageEvent")
    if car_soc < 80:
        assert posts == []
    else:
        assert len(posts) == 1
        assert parse_qs(posts[0][2]) == {"pageevent": ["evseEnabled"], "evseEnabled": ["1"]}
    assert float(hass.states.get("number.eveus_ev_charger_initial_soc").state) == car_soc


async def test_renamed_initial_soc_is_filled_before_the_stop_decision(
    hass, aioclient_mock, monkeypatch,
) -> None:
    """A rename re-adds Initial SOC, so its poll listener now runs after the
    SOC limit's: the Stop must still wait for this poll's car reading."""
    from custom_components.eveus.switch import EveusSocLimitSwitch

    entry = _entry(hass, HOST_A)
    hass.config_entries.async_update_entry(entry, data={
        **entry.data, CONF_SOC_MODE: SOC_MODE_ADVANCED,
        CONF_INITIAL_SOC: 85, CONF_TARGET_SOC: 80, CONF_BATTERY_CAPACITY: 60,
        CONF_SOC_CORRECTION: 0, CONF_EXTERNAL_SOC_ENTITY: "sensor.car_soc",
    })
    hass.states.async_set("sensor.car_soc", "25")
    monkeypatch.setattr(EveusSocLimitSwitch, "async_get_last_state", AsyncMock(
        return_value=SimpleNamespace(state="on"),
    ))
    payload = {**REAL_MAIN, "state": 2, "evseEnabled": 0, "suspendLimits": 0,
               "sessionEnergy": 0, "sessionTime": 0}
    _mock_charger(aioclient_mock, HOST_A, json=payload)
    await _setup(hass, entry)
    er.async_get(hass).async_update_entity(
        "number.eveus_ev_charger_initial_soc", new_entity_id="number.car_initial_soc",
    )
    await hass.async_block_till_done()
    aioclient_mock.clear_requests()
    _mock_charger(aioclient_mock, HOST_A, json={**payload, "state": 4, "sessionTime": 30})
    await entry.runtime_data.updater.async_refresh()
    await hass.async_block_till_done()
    assert _calls(aioclient_mock, HOST_A, "/pageEvent") == []
    assert float(hass.states.get("number.car_initial_soc").state) == 25


async def test_car_sensor_recovery_does_not_replace_manual_soc_or_trigger_stop(
    hass, aioclient_mock,
) -> None:
    entry = _entry(hass, HOST_A)
    hass.config_entries.async_update_entry(entry, data={
        **entry.data, CONF_SOC_MODE: SOC_MODE_ADVANCED,
        CONF_INITIAL_SOC: 20, CONF_TARGET_SOC: 80, CONF_BATTERY_CAPACITY: 60,
        CONF_SOC_CORRECTION: 0, CONF_EXTERNAL_SOC_ENTITY: "sensor.car_soc",
    })
    hass.states.async_set("sensor.car_soc", "unavailable")
    _mock_charger(aioclient_mock, HOST_A, json={
        **REAL_MAIN, "state": 4, "evseEnabled": 0, "suspendLimits": 0,
        "sessionEnergy": 0, "sessionTime": 30,
    })
    await _setup(hass, entry)
    await hass.services.async_call("switch", "turn_on", {
        "entity_id": "switch.eveus_ev_charger_limit_soc_enabled",
    }, blocking=True)
    await _set_current(hass, "number.eveus_ev_charger_initial_soc", 42)
    hass.states.async_set("sensor.car_soc", "85")
    aioclient_mock.clear_requests()
    await entry.runtime_data.updater.async_refresh()
    await hass.async_block_till_done()
    assert _calls(aioclient_mock, HOST_A, "/pageEvent") == []
    assert float(hass.states.get("number.eveus_ev_charger_initial_soc").state) == 42


async def test_meter_tail_reaches_ha_before_reset_and_cost_window_changes(
    hass, aioclient_mock, monkeypatch
):
    """Observe actual HA state events, including the buffered tail and reset."""
    from custom_components.eveus import sensor_definitions as sd

    clock = [dt_util.utcnow().replace(second=0, microsecond=0)]
    monkeypatch.setattr(sd, 'dt_util', SimpleNamespace(utcnow=lambda: clock[0], parse_datetime=dt_util.parse_datetime))
    entry = _entry(hass, HOST_A)
    payload = {**REAL_MAIN, 'state':4, 'evseEnabled':0, 'sessionEnergy':10,
               'sessionMoney':10, 'sessionTime':100, 'totalEnergy':100}
    _mock_charger(aioclient_mock, HOST_A, json=payload)
    await _setup(hass, entry)
    updater = entry.runtime_data.updater
    await updater.async_refresh()
    entity_id = 'sensor.eveus_ev_charger_session_cost'
    rows = []
    @callback
    def record_meter_state(event):
        if event.data['entity_id'] == entity_id:
            rows.append(event.data['new_state'])

    unsub = hass.bus.async_listen('state_changed', record_meter_state)
    clock[0] += timedelta(seconds=30)
    aioclient_mock.clear_requests()
    _mock_charger(aioclient_mock, HOST_A, json={**payload, 'sessionMoney':11})
    await updater.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == '10.0'
    assert rows == []
    clock[0] += timedelta(seconds=5)
    aioclient_mock.clear_requests()
    _mock_charger(aioclient_mock, HOST_A, json={**payload, 'sessionMoney':0, 'sessionTime':0})
    await updater.async_refresh()
    await hass.async_block_till_done()
    unsub()
    assert [row.state for row in rows] == ['11.0', '0.0']
    assert hass.states.get(entity_id).state == '0.0'
    assert rows[0].attributes['last_reset'] < rows[1].attributes['last_reset']


@pytest.mark.parametrize("key,field,recovered", [
    # A lifetime meter never comes back lower: a drop is held (meter_guard).
    ("total_energy", "totalEnergy", 10), ("session_cost", "sessionMoney", 0),
])
async def test_missing_meter_is_unavailable_and_recovers_to_valid_zero(
    hass, aioclient_mock, key, field, recovered
):
    entry = _entry(hass, HOST_A)
    payload = {**REAL_MAIN, field: 10}
    _mock_charger(aioclient_mock, HOST_A, json=payload)
    await _setup(hass, entry)
    updater = entry.runtime_data.updater
    entity_id = f"sensor.eveus_ev_charger_{key}"
    assert hass.states.get(entity_id).state == "10.0"
    aioclient_mock.clear_requests()
    _mock_charger(aioclient_mock, HOST_A, json={k: v for k, v in payload.items() if k != field})
    await updater.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    rows = []

    @callback
    def capture(event):
        if event.data["entity_id"] == entity_id:
            rows.append(event.data["new_state"].state)

    unsub = hass.bus.async_listen("state_changed", capture)
    aioclient_mock.clear_requests()
    aioclient_mock.post(f"http://{HOST_A}/main", exc=asyncio.TimeoutError())
    await updater.async_refresh()
    await hass.async_block_till_done()
    assert updater.visible_within(AVAILABILITY_GRACE_PERIOD)
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    assert rows == []
    aioclient_mock.clear_requests()
    _mock_charger(aioclient_mock, HOST_A, json={**payload, field: recovered})
    updater._next_poll_attempt = 0
    await updater.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == f"{recovered:.1f}"
    assert rows == [f"{recovered:.1f}"]
    unsub()


async def test_a_restart_holds_a_zero_boot_reading_against_the_saved_meters(
    hass, hass_storage, aioclient_mock
):
    """The poll after a restart can be the charger's zero boot reading: the
    last accepted readings come from storage, so it does not become the baseline."""
    entry = _entry(hass, HOST_A)
    hass_storage[f"eveus_meters_{entry.entry_id}"] = {
        "version": 1,
        "data": {"totalEnergy": 5440.86, "IEM1": 49.16},
    }
    _mock_charger(aioclient_mock, HOST_A, json={**REAL_MAIN, "totalEnergy": 0, "IEM1": 0})
    await _setup(hass, entry)

    assert hass.states.get("sensor.eveus_ev_charger_total_energy").state == "5440.86"
    assert hass.states.get("sensor.eveus_ev_charger_counter_a_energy").state == "49.16"


async def test_unloading_saves_the_accepted_meters_for_the_next_start(
    hass, hass_storage, aioclient_mock
):
    entry = _entry(hass, HOST_A)
    _mock_charger(aioclient_mock, HOST_A, json={**REAL_MAIN, "totalEnergy": 5107.83})
    await _setup(hass, entry)
    # Inside the save interval: nothing is written by the poll itself.
    aioclient_mock.clear_requests()
    _mock_charger(aioclient_mock, HOST_A, json={**REAL_MAIN, "totalEnergy": 5108.4})
    await entry.runtime_data.updater.async_refresh()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    # What Home Assistant does as it stops: write whatever is still pending.
    hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
    await hass.async_block_till_done()

    assert hass_storage[f"eveus_meters_{entry.entry_id}"]["data"]["totalEnergy"] == pytest.approx(5108.4)


async def test_last_session_duration_shown_in_hours_survives_a_restart(
    hass, aioclient_mock
) -> None:
    """Home Assistant saves the state in the unit it is shown in; the README
    suggests hours. A restart must bring back the same duration."""
    entity_id = "sensor.eveus_ev_charger_last_session_duration"
    entry = _entry(hass, HOST_A)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", DOMAIN, "eveus_last_session_duration", config_entry=entry,
        suggested_object_id="eveus_ev_charger_last_session_duration",
    )
    registry.async_update_entity_options(entity_id, "sensor", {"unit_of_measurement": "h"})
    mock_restore_cache(hass, [State(entity_id, "14.256944444444445", {
        "unit_of_measurement": "h", "reason": "complete",
        "finished_at": "2026-10-25T14:05:46+02:00",
    })])
    _mock_charger(aioclient_mock, HOST_A)
    await _setup(hass, entry)

    state = hass.states.get(entity_id)
    assert state.attributes["unit_of_measurement"] == "h"
    assert float(state.state) == pytest.approx(14.256944, rel=1e-6)


async def test_removing_the_entry_leaves_no_meter_readings_behind(
    hass, hass_storage, aioclient_mock
):
    """A save still waiting on the removed entry's own store must not write
    the file again after removal deleted it."""
    entry = _entry(hass, HOST_A)
    _mock_charger(aioclient_mock, HOST_A, json={**REAL_MAIN, "totalEnergy": 5107.83})
    await _setup(hass, entry)
    aioclient_mock.clear_requests()
    _mock_charger(aioclient_mock, HOST_A, json={**REAL_MAIN, "totalEnergy": 5108.4})
    await entry.runtime_data.updater.async_refresh()

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5))
    await hass.async_block_till_done()
    hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
    await hass.async_block_till_done()

    assert f"eveus_meters_{entry.entry_id}" not in hass_storage
