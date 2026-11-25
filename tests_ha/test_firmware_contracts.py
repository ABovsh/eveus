"""Replay independently sourced firmware behaviour through Home Assistant."""

from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
from homeassistant.helpers.entity_platform import async_get_platforms

from custom_components.eveus import common_command
from custom_components.eveus.const import CONF_PHASES, DOMAIN

from .conftest import _entry, _mock_charger, _setup

_directory = os.environ.get("EVEUS_FW_CONTRACTS_DIR")
if not _directory:
    pytest.skip("firmware contracts are unavailable", allow_module_level=True)

_contracts = sorted(Path(_directory).glob("*.json"))
if not _contracts:
    pytest.fail("firmware contracts are missing", pytrace=False)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

HOST = "charger.example"
URL = f"http://{HOST}"


def _payload(directory, value):
    if isinstance(value, str):
        return json.loads((directory / "payloads" / value).read_bytes())
    return value


def _mock_poll(http, directory, poll):
    main = _payload(directory, poll["main"]) | poll.get("main_overrides", {})
    _mock_charger(http, HOST, json=main)
    if "init" in poll:
        http.post(f"{URL}/init", json=_payload(directory, poll["init"]))
    else:
        http.post(f"{URL}/init", status=404)
    if "graphql" in poll:
        http.post(f"{URL}/graphql", json=_payload(directory, poll["graphql"]))


def _entities(hass, entry):
    return {
        f"{platform.domain}.{entity.translation_key}": entity.entity_id
        for platform in async_get_platforms(hass, DOMAIN)
        if platform.config_entry is entry
        for entity in platform.entities.values()
    }


def _json_body(data):
    body = json.loads(data) if isinstance(data, str) else data
    if isinstance(body.get("query"), str):
        body = {**body, "query": " ".join(body["query"].split())}
    return body


def _wire_difference(calls, expected):
    if len(calls) != len(expected):
        return "wire.count"
    for index, ((method, url, data, headers), want) in enumerate(zip(calls, expected), 1):
        checks = (
            ("method", method.upper(), want["method"]),
            ("route", url.raw_path_qs, want["route"]),
            ("headers", {key.lower(): value for key, value in (headers or {}).items()}, want["headers"]),
        )
        for field, actual, value in checks:
            if actual != value:
                return f"wire[{index}].{field}"
        body = want["body"]
        if isinstance(body, dict):
            try:
                data = _json_body(data)
            except (ValueError, TypeError, AttributeError):
                return f"wire[{index}].body"
            if json.dumps(data, sort_keys=True) != json.dumps(_json_body(body), sort_keys=True):
                return f"wire[{index}].body"
        elif data != body:
            return f"wire[{index}].body"
    return None


def _requested_graphql_fields(calls, required):
    for method, url, data, _ in calls:
        if method.upper() != "POST" or url.path != "/graphql":
            continue
        try:
            body = _json_body(data)
        except (ValueError, TypeError, AttributeError):
            continue
        if body.get("operationName") == "StationData":
            fields = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", body.get("query", "")))
            if {"stationData", *required} <= fields:
                return True
    return False


def _difference(hass, entry, calls, events, expect):
    if "wire" in expect and (difference := _wire_difference(calls, expect["wire"])):
        return difference
    if "graphql_fields" in expect and not _requested_graphql_fields(calls, expect["graphql_fields"]):
        return "graphql_fields"
    if any(events[event] != count for event, count in expect.get("events", {}).items()):
        return "events"

    entity_ids = _entities(hass, entry)
    for key, spec in expect.get("entities", {}).items():
        entity_id = entity_ids.get(key)
        state = hass.states.get(entity_id) if entity_id else None
        if state is None:
            return f"entities.{key}"
        if "state" in spec and state.state != spec["state"]:
            return f"entities.{key}.state"
        for attr, value in spec.get("attributes", {}).items():
            if attr not in state.attributes or state.attributes[attr] != value:
                return f"entities.{key}.attributes.{attr}"
    return None


@pytest.mark.parametrize("path", _contracts, ids=lambda path: path.stem)
async def test_firmware_contract(hass, aioclient_mock, caplog, monkeypatch, path):
    field = "document"
    advisory = False
    events = Counter()

    def count_event(event):
        events[event.event_type] += 1

    try:
        item = json.loads(path.read_bytes())
        advisory = item.get("status") == "needs-evidence"
        for event_type in {event for step in item["steps"] for event in step["expect"].get("events", {})}:
            hass.bus.async_listen(event_type, count_event)

        entry = _entry(hass, HOST)
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_PHASES: item.get("phases", 1)})
        now = time.monotonic()
        monkeypatch.setattr(common_command, "time", SimpleNamespace(monotonic=lambda: now))

        for index, step in enumerate(item["steps"], 1):
            prefix = f"steps[{index}]"
            aioclient_mock.clear_requests()
            events.clear()
            now += 1  # Advance the command limiter without sleeping.
            if "poll" in step:
                field = f"{prefix}.poll"
                _mock_poll(aioclient_mock, path.parent, step["poll"])
                if index == 1:
                    await _setup(hass, entry)
                else:
                    await entry.runtime_data.updater.async_refresh()
                    await hass.async_block_till_done()
                if not entry.runtime_data.updater.last_update_success:
                    break
            else:
                field = f"{prefix}.call"
                call = step["call"]
                if "reply" in call:
                    aioclient_mock.post(f"{URL}/graphql", json=call["reply"])
                aioclient_mock.post(re.compile(rf"^{re.escape(URL)}/"), text="ok")
                entity_id = _entities(hass, entry)[call["entity"]]
                domain, service = call["service"].split(".")
                await hass.services.async_call(
                    domain, service, {**call.get("data", {}), "entity_id": entity_id}, blocking=True,
                )
                await hass.async_block_till_done()

            field = f"{prefix}.expect"
            mismatch = _difference(hass, entry, aioclient_mock.mock_calls, events, step["expect"])
            if mismatch:
                field = f"{field}.{mismatch}"
                break
        else:
            return
    except Exception:
        # Exceptions may contain request bodies or entity state values.
        pass
    finally:
        caplog.clear()

    message = f"{path.stem}: {field}"
    if advisory:
        pytest.xfail(message)
    pytest.fail(message, pytrace=False)
