"""Record the Eveus behavior exercised by the private firmware check."""

import json
import os
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest
from freezegun import freeze_time
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

from custom_components.eveus import button, common_command, common_network
from custom_components.eveus.const import DOMAIN
from custom_components.eveus.reve import FAMILY_REVE

from .test_firmware_profiles import (
    HOST,
    URL,
    _assert_no_errors,
    _command,
    _entities,
    _mock_replies,
    _request_shape,
    _service_call,
    _start,
)
from .test_firmware_profiles import profile as profile

pytestmark = [
    pytest.mark.skipif(not os.environ.get("EVEUS_FW_BEHAVIOR_OUT"), reason="firmware check only"),
    pytest.mark.usefixtures("enable_custom_integrations"),
]

_CAPABILITIES = ("min", "max", "step", "options", "unit_of_measurement", "device_class", "state_class")
_WRITABLE = {"button", "number", "select", "switch", "time"}
_START = datetime(2025, 1, 1, tzinfo=UTC)
_SOCKET_CURRENTS = {"currentSet", "sh1CurrentValue", "sh2CurrentValue"}
_FRACTIONAL_LIMITS = {"moneyLimit", "sh1EnergyValue", "sh2EnergyValue"}


@pytest.fixture
def replay_clock(hass):
    # Freeze time after HA has finished setting up its own timer fixtures.
    with freeze_time(_START, real_asyncio=True) as clock:
        yield clock


def _snapshot(hass, entry, entities, stage):
    for item in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id):
        state = hass.states.get(item.entity_id)
        attributes = dict(item.capabilities or {})
        attributes.update(
            unit_of_measurement=item.unit_of_measurement,
            device_class=item.original_device_class,
        )
        if state is not None:
            attributes.update(state.attributes)
        capabilities = {key: attributes[key] for key in _CAPABILITIES if key in attributes
                        and attributes[key] is not None}
        observation = entities.setdefault(item.unique_id, {
            "platform": item.domain,
            "enabled_by_default": not item.disabled,
            "capabilities": capabilities,
            "states": {},
        })
        assert observation["platform"] == item.domain, f"duplicate unique ID {item.unique_id}"
        observation["states"][stage] = state.state if state is not None else None
        if capabilities != observation["capabilities"]:
            observation.setdefault("capability_changes", {})[stage] = capabilities


def _wire(call):
    shape = _request_shape(call)
    _, url, data, _ = call
    if shape["encoding"] == "json":
        body = data if isinstance(data, dict) else json.loads(data)
        if isinstance(body.get("query"), str):
            body = {**body, "query": " ".join(body["query"].split())}
    elif shape["encoding"] == "form-urlencoded":
        body = parse_qs(data or "", keep_blank_values=True)
    else:
        body = data
    return {"method": shape["method"], "route": url.raw_path_qs,
            "headers": shape["headers"], "body": body}


def _write_cases(entity, capabilities, *, legacy=False):
    domain = entity.entity_id.partition(".")[0]
    target = {"entity_id": entity.entity_id}
    if domain == "number":
        low, high, step = (capabilities[key] for key in ("min", "max", "step"))
        mid = round(low + round((high - low) / (2 * step)) * step, 10)
        cases = {"min": low, "mid": mid, "max": high}
        if entity.native_unit_of_measurement == "A":
            cases["25A"] = 25
        if legacy and _command(entity) in _FRACTIONAL_LIMITS:
            cases["fractional"] = 10.5
        for name, value in cases.items():
            yield name, "set_value", {**target, "value": value}
    elif domain == "switch":
        for value in ("on", "off"):
            yield value, f"turn_{value}", target
    elif domain == "select":
        for option in entity.options:
            yield option, "select_option", {**target, "option": option}
    elif domain == "time":
        for value in ("00:00:00", "12:00:00", "23:59:00"):
            yield value, "set_value", {**target, "time": value}
    else:
        _, service, data = _service_call(entity)
        yield service, service, data


async def _write_controls(hass, entry, http, entities, monkeypatch, *, socket_off=None):
    # Probe default-disabled controls too. Record their default and published
    # states before enabling them through the entity registry for the writes.
    registry = er.async_get(hass)
    disabled = [item for item in er.async_entries_for_config_entry(registry, entry.entry_id)
                if item.domain in _WRITABLE and item.disabled]
    for item in disabled:
        registry.async_update_entity(item.entity_id, disabled_by=None)
    if disabled:
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

    # Polls are exercised above. Keep scheduled refreshes from being recorded
    # under whichever service happens to be running when their timers fire.
    updater = entry.runtime_data.updater
    monkeypatch.setattr(updater, "_schedule_post_command_refresh", lambda: None)
    monkeypatch.setattr(button, "time", SimpleNamespace(time=_START.timestamp))
    # Advance the command limiter alone so a scheduled HA poll cannot become
    # due merely because the command sequence advances its test clock.
    now = time.monotonic()
    monkeypatch.setattr(common_command, "time", SimpleNamespace(monotonic=lambda: now))

    async def record_write(entity, cases, name, service, data):
        nonlocal now
        now += 1
        start = len(http.mock_calls)
        result = cases[name] = {
            "input": {key: value for key, value in data.items() if key != "entity_id"},
            "requests": [],
        }
        domain = entity.entity_id.partition(".")[0]
        try:
            if domain == "number":
                # Exercise the HA entity's write even if a poll lowered its
                # advertised bound after the requested value was chosen.
                await entity.async_set_native_value(data["value"])
            else:
                await hass.services.async_call(domain, service, data, blocking=True)
            await hass.async_block_till_done()
        except HomeAssistantError as error:
            result["error"] = f"{type(error).__name__}: {error}"
        result["requests"] = [_wire(call) for call in http.mock_calls[start:]]

    controls = {entity.unique_id: entity for entity in _entities(hass, entry)}
    for key, observation in sorted(entities.items()):
        if observation["platform"] not in _WRITABLE:
            continue
        entity = controls[key]
        cases = observation["writes"] = {}
        for name, service, data in _write_cases(
            entity, observation["capabilities"], legacy=socket_off is not None,
        ):
            await record_write(entity, cases, name, service, data)

    if socket_off is not None:
        # An accepted switch-off command alone cannot lift the cap.
        await _poll(hass, http, entry, socket_off)
        for key, entity in controls.items():
            if _command(entity) in _SOCKET_CURRENTS:
                await record_write(
                    entity, entities[key]["writes"], "25A_socket_off", "set_value",
                    {"entity_id": entity.entity_id, "value": 25},
                )


def _mock_scenario(http, profile, *, init_failed, graphql_failed):
    if init_failed:
        http.post(f"{URL}/init", status=503)
    if profile["family"] != FAMILY_REVE:
        return

    async def graphql(method, url, data):
        operation = (data if isinstance(data, dict) else json.loads(data))["operationName"]
        if operation == "UpdateStationData":
            payload = {"data": {"updateStationData": True}}
        elif operation == "ListSessions":
            payload = {"data": {"listSessions": {"items": []}}}
        else:
            if graphql_failed:
                return AiohttpClientMockResponse(method, url, status=503)
            payload = profile["graphql"]
        return AiohttpClientMockResponse(method, url, json=payload)

    http.post(f"{URL}/graphql", side_effect=graphql)


def _route_counts(http):
    return Counter(url.path for _, url, _, _ in http.mock_calls)


async def _poll(hass, http, entry, profile, *, init_failed=False, graphql_failed=False):
    http.clear_requests()
    _mock_scenario(http, profile, init_failed=init_failed, graphql_failed=graphql_failed)
    _mock_replies(http, profile, allow_writes=True)
    await entry.runtime_data.updater.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    return _route_counts(http)


def _save(profile, fingerprint, *, scenario="profiles"):
    directory = Path(os.environ["EVEUS_FW_BEHAVIOR_OUT"]) / scenario
    (directory / f"{profile['label']}.json").write_text(
        json.dumps(fingerprint, allow_nan=False), encoding="utf-8",
    )


async def test_firmware_behavior(hass, aioclient_mock, caplog, replay_clock, profile, monkeypatch):
    # A 32 A charger makes a 25 A command meaningful even if the fixture's
    # captured design current is 16 A.
    main = {**profile["main"], "curDesign": {"value": 32}}
    profile = {**profile, "main": main}
    http = aioclient_mock
    if profile["label"].startswith(("1x-", "2x-")):
        fingerprint = await _legacy_behavior(hass, http, caplog, replay_clock, profile, monkeypatch)
        _save(profile, fingerprint)
        return

    _mock_scenario(http, profile, init_failed=True, graphql_failed=False)
    entry = await _start(hass, http, profile, caplog, allow_writes=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    entities = {}
    requests = {"setup": _route_counts(http)}
    _snapshot(hass, entry, entities, "setup")

    if "restricted_mode" in main:
        main["restricted_mode"] = {"value": 1}
    # The REVE read outage lasts one poll. The last poll is past its
    # 30-minute optional-read retry window.
    for stage, delay in (
        ("poll", 30),
        ("read_failure", 30),
        ("retry_1", 301),
        ("retry_2", 301),
        ("recovery", 1801),
    ):
        replay_clock.tick(delay)
        requests[stage] = await _poll(
            hass, http, entry, profile, init_failed=stage != "recovery",
            graphql_failed=stage == "read_failure",
        )
        _snapshot(hass, entry, entities, stage)

    await _write_controls(hass, entry, http, entities, monkeypatch)
    _assert_no_errors(caplog, "firmware behavior")
    _save(profile, {"entities": entities, "requests": requests})


async def _legacy_behavior(hass, http, caplog, clock, profile, monkeypatch):
    # Drive polls explicitly so a Stop transition cannot schedule extra reads
    # that land in a later timed /init sample.
    monkeypatch.setattr(common_network.EveusUpdater, "_schedule_refresh", lambda self: None)
    monkeypatch.setattr(common_network.EveusUpdater, "_schedule_post_command_refresh", lambda self: None)
    main = profile["main"]
    main.update({key: {"value": value} for key, value in {
        "restricted_mode": 0,
        "limitsStatus": 0x2C,  # Cost limit, Schedule 1 and its energy limit.
        "moneyLimit": 8.5,
        "energySchedule1": 4.5,
        "energySchedule2": 6.3,
    }.items()})
    init = dict(profile["init"])
    if "ESP_SW_version" in init:
        # Exercise ESP identification without an MCU version to fall back to.
        init.pop("MCU_SW_version", None)
    init["minVoltage"] = {"value": 180}
    profile = {**profile, "init": init}
    entry = await _start(hass, http, profile, caplog, allow_writes=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    firmware_version = next(
        (device.sw_version for device in devices if (DOMAIN, HOST) in device.identifiers), None,
    )
    entities = {}
    requests = {"setup": _route_counts(http)}
    _snapshot(hass, entry, entities, "setup")

    main.update({key: {"value": value} for key, value in {
        "restricted_mode": 1, "state": 20, "pilot": 2, "limitsStatus": 0x22C,
    }.items()})
    for stage, delay, voltage in (
        ("stop", 30, 180),
        ("unknown_pilot", 869, 170),  # 899 seconds since setup.
        ("init_refresh", 1, 170),
        ("init_unusable", 900, 160),
        ("init_backoff", 299, 160),
        ("init_recovery", 1, 160),
    ):
        clock.tick(delay)
        if stage == "unknown_pilot":
            main["pilot"] = {"value": 0}
        init["minVoltage"] = {"value": voltage}
        reply = {"minVoltage": init["minVoltage"]} if stage == "init_unusable" else init
        requests[stage] = await _poll(hass, http, entry, {**profile, "init": reply})
        _snapshot(hass, entry, entities, stage)

    socket_off = {**profile, "main": {**main, "restricted_mode": {"value": 0}}}
    await _write_controls(hass, entry, http, entities, monkeypatch, socket_off=socket_off)
    _assert_no_errors(caplog, "legacy firmware behavior")
    return {"entities": entities, "requests": requests, "firmware_version": firmware_version}


async def test_classic_init_retry(hass, aioclient_mock, caplog, replay_clock, profile):
    if profile["family"] == FAMILY_REVE:
        pytest.skip("classic /init firmware probe")
    # An R3 Wi-Fi version can identify the charger without its controller
    # version. For pre-R3, /init identifies it after two five-minute retry windows.
    early = {**profile, "main": {key: value for key, value in profile["main"].items()
                               if key not in {"verFWMain", "firmware"}}}
    http = aioclient_mock
    _mock_scenario(http, early, init_failed=True, graphql_failed=False)
    entry = await _start(hass, http, early, caplog, allow_writes=True)
    requests = {"setup": _route_counts(http)}
    for stage, delay in (("poll", 30), ("retry_1", 301), ("retry_2", 301), ("recovery", 301)):
        replay_clock.tick(delay)
        recovered = stage == "recovery"
        requests[stage] = await _poll(
            hass, http, entry, profile if recovered else early, init_failed=not recovered,
        )
    _save(profile, requests, scenario="init_retry")
