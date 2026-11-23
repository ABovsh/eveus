"""Run real Home Assistant scenarios against firmware simulation profiles."""

from __future__ import annotations

import json
import logging
import os
import re
import time as real_time
from datetime import UTC, datetime, time
from operator import eq, ne
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import async_get_platforms

from custom_components.eveus import button, common_command
from custom_components.eveus.const import CONF_MODEL, CONF_PHASES, DOMAIN, MODEL_MAX_CURRENT
from custom_components.eveus.reve import FAMILY_REVE

from .conftest import _entry, _setup

_directory = os.environ.get("EVEUS_FW_SIM_DIR")
if not _directory or not Path(_directory).is_dir():
    pytest.skip(
        "EVEUS_FW_SIM_DIR must point to a firmware simulation profiles directory",
        allow_module_level=True,
    )
_profiles = sorted(path for path in Path(_directory).glob("*.json") if path.is_file())
if not _profiles:
    pytest.skip(
        "EVEUS_FW_SIM_DIR contains no firmware simulation profiles", allow_module_level=True
    )

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

HOST = "charger.example"
URL = f"http://{HOST}"
LOGGER = f"custom_components.{DOMAIN}"


@pytest.fixture(params=_profiles, ids=lambda path: path.stem)
def profile(request):
    data = json.loads(request.param.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    if data["family"] == FAMILY_REVE:
        # A combined stationData reply can serve both REVE read documents.
        if not data.get("graphql", {}).get("data", {}).get("stationData"):
            pytest.skip("no GraphQL replies in profile")
    elif not data["main"]:
        pytest.skip("no /main keys in profile")
    return data


def _values(fields):
    return {key: field["value"] for key, field in fields.items()}


def _mock_replies(http, profile, overrides=None, *, allow_writes=False):
    overrides = overrides or {}
    if profile["main"]:
        http.post(f"{URL}/main", json={**_values(profile["main"]), **overrides})
    else:
        http.post(f"{URL}/main", status=404)
    if profile["init"]:
        http.post(f"{URL}/init", json=_values(profile["init"]))
    else:
        http.post(f"{URL}/init", status=404)
    if profile["family"] == FAMILY_REVE:
        reply = profile["graphql"]
        data = reply["data"]
        station = dict(data["stationData"])
        for key, value in overrides.items():
            station[{"state": "stationState", "subState": "stationSubState"}[key]] = value
        http.post(f"{URL}/graphql", json={**reply, "data": {**data, "stationData": station}})
    if allow_writes:
        # Register after the read routes so every command POST is recorded,
        # even if the integration writes to a route absent from the profile.
        http.post(re.compile(rf"^{re.escape(URL)}/"), text="ok")


def _assert_no_errors(caplog, context):
    errors = [
        f"{record.name}: {record.getMessage()}"
        for record in caplog.records
        if (record.name == LOGGER or record.name.startswith(f"{LOGGER}."))
        and (record.levelno >= logging.ERROR or record.exc_info or record.exc_text)
    ]
    assert not errors, "\n".join([context, *errors])


def _assert_poll(updater, http, profile, caplog, context):
    assert updater.last_update_success and updater.available, context
    assert updater.firmware_family == profile["family"], context
    if profile["family"] == FAMILY_REVE:
        assert updater.reve_diagnostics()["measurements"]["last_source"] == "graphql", context
        route = "/graphql"
    else:
        route = "/main"
    assert any(url.path == route for _, url, _, _ in http.mock_calls), context
    _assert_no_errors(caplog, context)


async def _start(hass, http, profile, caplog, **mock_options):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    _mock_replies(http, profile, **mock_options)
    entry = _entry(hass, HOST)
    main = _values(profile["main"])
    data = dict(entry.data)
    # Expose every per-phase entity backed by a field in the profile.
    if main.keys() & {"curMeas2", "curMeas3", "voltMeas2", "voltMeas3"}:
        data[CONF_PHASES] = 3
    design_current = main.get("curDesign")
    for model, current in MODEL_MAX_CURRENT.items():
        if design_current == current:
            data[CONF_MODEL] = model
            break
    hass.config_entries.async_update_entry(entry, data=data)
    await _setup(hass, entry)
    _assert_poll(entry.runtime_data.updater, http, profile, caplog, "first charger poll")
    return entry


def _entities(hass, entry):
    return [
        entity
        for platform in async_get_platforms(hass, DOMAIN)
        if platform.config_entry is entry
        for entity in platform.entities.values()
    ]


async def test_first_poll(hass, aioclient_mock, caplog, profile):
    entry = await _start(hass, aioclient_mock, profile, caplog)
    if profile["family"] == FAMILY_REVE:
        return

    if profile["init"]:
        assert any(url.path == "/init" for _, url, _, _ in aioclient_mock.mock_calls)
    unavailable = []
    entities = _entities(hass, entry)
    assert entities, "no charger entities loaded"
    for entity in entities:
        sources = profile["main"].keys() & entity._source_keys()
        if sources:
            state = hass.states.get(entity.entity_id)
            if state is None or state.state == STATE_UNAVAILABLE:
                unavailable.append(f"{entity.entity_id} ({', '.join(sorted(sources))})")
    assert not unavailable, "Unavailable after first poll: " + ", ".join(unavailable)


def _substate_parent(group, profile):
    conditions = profile.get("subState_selectors", {}).get(group, ())
    if not conditions:
        return None
    operators = {"==": eq, "!=": ne}
    states = profile["statuses"].get("state", {})
    for state in dict.fromkeys([*map(int, states), *range(256)]):
        if all(operators[c["operator"]](state, c["state"]) for c in conditions):
            return state
    pytest.fail(f"No state satisfies the selectors for {group}")


def _status_cases(profile):
    for group, table in profile["statuses"].items():
        if group == "state":
            for code in table:
                yield group, "state", {"state": int(code)}
        elif group.startswith("subState"):
            parent = _substate_parent(group, profile)
            for code in table:
                payload = {"subState": int(code)}
                if parent is not None:
                    payload["state"] = parent
                yield group, "substate", payload


async def test_status_sweep(hass, aioclient_mock, caplog, profile):
    cases = list(_status_cases(profile))
    if not cases:
        pytest.skip("no state or subState tables in profile")
    entry = await _start(hass, aioclient_mock, profile, caplog)
    updater = entry.runtime_data.updater
    sensors = {
        entity.translation_key: entity.entity_id
        for entity in _entities(hass, entry)
        if entity.entity_id.startswith("sensor.")
    }
    for group, sensor, overrides in cases:
        context = f"{group}: {overrides}"
        aioclient_mock.clear_requests()
        _mock_replies(aioclient_mock, profile, overrides)
        await updater.async_refresh()
        await hass.async_block_till_done()
        _assert_poll(updater, aioclient_mock, profile, caplog, context)
        state = hass.states.get(sensors[sensor])
        assert state is not None and state.state and state.state != STATE_UNAVAILABLE, context


def _command(entity):
    if isinstance(entity, button.EveusSyncTimeButton):
        return "systemTime"
    description = getattr(entity, "entity_description", None)
    return (
        getattr(description, "command", None)
        or getattr(entity, "WRITE_KEY", None)
        or getattr(entity, "_command", None)
    )


def _service_call(entity):
    domain = entity.entity_id.partition(".")[0]
    target = {"entity_id": entity.entity_id}
    if domain == "switch":
        return domain, "turn_off" if entity.is_on else "turn_on", target
    if domain == "number":
        low, high = entity.native_min_value, entity.native_max_value
        value = min(low + entity.native_step, high)
        return (
            domain,
            "set_value",
            {**target, "value": low if value == entity.native_value else value},
        )
    if domain == "select":
        option = next(option for option in entity.options if option != entity.current_option)
        return domain, "select_option", {**target, "option": option}
    if domain == "time":
        value = time(12) if entity.native_value != time(12) else time(13)
        return domain, "set_value", {**target, "time": value.isoformat()}
    assert domain == "button", entity.entity_id
    return domain, "press", target


def _request_shape(call):
    method, url, data, headers = call
    headers = {key.lower(): value for key, value in (headers or {}).items()}
    content_type = headers.get("content-type", "").partition(";")[0].strip().lower()
    shape = {"route": url.path, "method": method.upper(), "headers": headers}
    if content_type == "application/x-www-form-urlencoded":
        fields = parse_qs(data or "", keep_blank_values=True)
        binding = fields.get("pageevent", [])
        return {
            **shape,
            "encoding": "form-urlencoded",
            "body_keys": list(fields),
            "body_binding": binding[0] if len(binding) == 1 else None,
        }
    if content_type == "application/json":
        body = data if isinstance(data, dict) else json.loads(data)
        return {**shape, "encoding": "json", "body_keys": list(body)}
    return {**shape, "encoding": content_type or None, "body_keys": []}


def _pageevent_equivalence(profile):
    # Current profiles already express scoped equivalence with accepted
    # `equivalent` variants. A profile can also declare the binding rule.
    return any(
        rule["route"] == "/pageEvent"
        and set(rule["bindings"]) == {"header", "body"}
        and all(
            field not in rule or rule[field] == profile.get(field)
            for field in ("family", "generation")
        )
        for rule in profile.get("transport_equivalences", ())
    )


def _header_bound(shape, command):
    headers = shape.get("headers", {})
    if (
        shape["route"] == "/pageEvent"
        and shape["method"] == "POST"
        and shape["encoding"] == "form-urlencoded"
        and shape.get("body_binding") == command
        and command != "pageevent"
        and not any(key.lower() == "pageevent" for key in headers)
        and {"pageevent", command} <= set(shape["body_keys"])
    ):
        return {
            **shape,
            "headers": {**headers, "pageevent": command},
            "body_keys": [key for key in shape["body_keys"] if key != "pageevent"],
            "body_binding": None,
        }
    return shape


def _matches(actual, expected):
    headers = {key.lower(): value for key, value in expected.get("headers", {}).items()}
    return (
        actual["route"] == expected["route"]
        and actual["method"] == expected["method"]
        and actual["encoding"] == expected["encoding"]
        and set(actual["body_keys"]) == set(expected["body_keys"])
        and actual.get("body_binding") == expected.get("body_binding")
        and actual["headers"].get("pageevent") == headers.get("pageevent")
        and all(actual["headers"].get(key) == value for key, value in headers.items())
    )


def _describe(shape):
    headers = {key.lower(): value for key, value in shape.get("headers", {}).items()}
    if "pageevent" in headers:
        binding = f"header={headers['pageevent']}"
    elif shape.get("body_binding"):
        binding = f"body={shape['body_binding']}"
    else:
        binding = "absent"
    fields = ", ".join(sorted(shape["body_keys"]))
    return f"{shape['method']} {shape['route']} {shape['encoding']} ({fields}), selector {binding}"


async def test_command_shapes(hass, aioclient_mock, caplog, monkeypatch, profile):
    if profile["family"] == FAMILY_REVE:
        pytest.skip("REVE profiles run setup and status only")
    writes = profile["writes"]
    accepted = {
        command: shapes
        for command, variants in writes.items()
        if (
            shapes := [
                item["shape"]
                for item in variants
                if item["acceptance"] in {"ui-observed", "equivalent"}
            ]
        )
    }
    if not accepted:
        pytest.skip("no accepted write variants in profile")
    now = real_time.monotonic()
    monkeypatch.setattr(common_command, "time", SimpleNamespace(monotonic=lambda: now))
    timestamp = datetime(2025, 1, 1, tzinfo=UTC).timestamp()
    monkeypatch.setattr(button, "time", SimpleNamespace(time=lambda: timestamp))
    equivalent = _pageevent_equivalence(profile)
    entry = await _start(hass, aioclient_mock, profile, caplog, allow_writes=True)
    failures = []
    binding_only = []
    checked = 0
    for entity in sorted(_entities(hass, entry), key=lambda entity: entity.entity_id):
        command = _command(entity)
        if command not in accepted:
            continue
        if isinstance(entity, button._EveusResetCounterButton) and (
            command in profile.get("destructive_commands", ())
            or any(item.get("destructive") for item in writes[command])
        ):
            continue

        checked += 1
        domain, service, data = _service_call(entity)
        start = len(aioclient_mock.mock_calls)
        now += 1  # Advance the command rate limiter without sleeping.
        try:
            await hass.services.async_call(domain, service, data, blocking=True)
            await hass.async_block_till_done()
        except HomeAssistantError as err:
            failures.append(f"{command}: {type(err).__name__}: {err}")

        actual = [_request_shape(call) for call in aioclient_mock.mock_calls[start:]]
        expected = accepted[command]
        if equivalent:
            actual = [_header_bound(shape, command) for shape in actual]
            expected = [_header_bound(shape, command) for shape in expected]
        if any(_matches(got, want) for got in actual for want in expected):
            continue
        if not equivalent and any(
            _matches(_header_bound(got, command), _header_bound(want, command))
            for got in actual
            for want in expected
        ):
            # Only the selector binding differs, and this firmware's acceptance
            # of the body binding is not established either way.
            binding_only.append(command)
            continue
        failures.append(
            f"{command}: sent {[_describe(s) for s in actual]}, "
            f"accepted {[_describe(s) for s in expected]}"
        )

    _assert_no_errors(caplog, "charger commands")
    if not checked:
        pytest.skip("no accepted variants for charger controls in profile")
    assert not failures, "\n".join(failures)
    if binding_only:
        pytest.xfail(
            f"{len(binding_only)} commands differ only in the /pageEvent selector "
            "binding (body vs header), whose acceptance is unknown for this firmware"
        )
