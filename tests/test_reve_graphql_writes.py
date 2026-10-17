"""REVE: settings written through GraphQL ``updateStationData``.

The input field and value type of every write come from the charger maker's
own apps (the local web app the charger serves, and my.ladergy.com): a toggle
sends 0/1 (PE control), a dedicated switch a boolean (One Charge), a list its
number (adaptive mode 0 off, 1 on, 2 percent), the time settings and session
limits nested objects. Session limits have no on/off flag on REVE: a limit is
on while it has a value and off when it is ``null``.

Counter A resets as the app does it, ``{iem: {IEM1: 1}}`` (checked live
2026-10-17). Still on ``/pageEvent`` until decided: "disable all limits", OCPP
and the Counter B reset (irreversible, not tried live).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest

from custom_components.eveus import reve
from test_reve_firmware import _Charger, _Response, _no_sleep, _reve_main, _updater


class _WritableCharger(_Charger):
    """Applies every mutation to the GraphQL station it answers with."""

    def post(self, url: str, **kwargs: Any) -> Any:
        import json

        if url.endswith("/graphql"):
            body = json.loads(kwargs["data"])
            if body["query"].lstrip().startswith("mutation"):
                _merge(self.station, body["variables"]["input"])
        return super().post(url, **kwargs)


class _NullRefusingCharger(_WritableCharger):
    """Like the real REVE: answers a limit's ``null`` with ``success: false``,
    and (``drops_limit``) drops the limit anyway."""

    drops_limit = True

    def post(self, url: str, **kwargs: Any) -> Any:
        import json

        if url.endswith("/graphql"):
            body = json.loads(kwargs["data"])
            if body["query"].lstrip().startswith("mutation"):
                change = body["variables"]["input"]
                if None in (change.get("session") or {}).values():
                    self.calls.append(("/graphql", body))
                    if self.drops_limit:
                        _merge(self.station, change)
                    return _Response(200, {"data": {"updateStationData": {
                        "__typename": "UpdateStationData", "success": False}}})
        return super().post(url, **kwargs)


def _merge(target: dict[str, Any], change: dict[str, Any]) -> None:
    for key, value in change.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value


def _known(monkeypatch: pytest.MonkeyPatch, charger: _Charger | None = None):
    charger = charger or _WritableCharger(_reve_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    charger.calls.clear()
    return charger, updater


def _send(updater: Any, command: str, value: Any) -> bool:
    return asyncio.run(updater._command_manager.send_command(command, value))


def _inputs(charger: _Charger) -> list[dict[str, Any]]:
    return [
        body["variables"]["input"]
        for path, body in charger.calls
        if path == "/graphql" and body["query"].lstrip().startswith("mutation")
    ]


@pytest.mark.parametrize(
    ("command", "value", "written"),
    [
        ("oneCharge", 1, {"isOneChargeEnabled": True}),
        ("oneCharge", 0, {"isOneChargeEnabled": False}),
        ("aiMode", 0, {"adaptiveModeStatus": 0}),
        ("aiMode", 1, {"adaptiveModeStatus": 1}),
        ("aiMode", 2, {"adaptiveModeStatus": 2}),
        ("aiVoltage", 215.0, {"voltageThreshold": 215}),
        ("minVoltage", 180, {"minVoltage": 180}),
        ("timeZone", 3, {"timeSettings": {"timeZone": 3}}),
        ("timeZone", -5, {"timeSettings": {"timeZone": -5}}),
        ("groundCtrl", 1, {"isGroundConnected": 1}),
        ("groundCtrl", 0, {"isGroundConnected": 0}),
        ("rstEM1", 0, {"iem": {"IEM1": 1}}),
        ("rstEM2", 0, {"iem": {"IEM2": 1}}),
    ],
)
def test_each_setting_is_one_graphql_write_in_the_apps_own_shape(
    monkeypatch: pytest.MonkeyPatch, command: str, value: Any, written: dict[str, Any]
) -> None:
    charger, updater = _known(monkeypatch)

    assert _send(updater, command, value) is True

    assert charger.paths() == ["/graphql"]
    assert _inputs(charger) == [written]


def test_time_sync_sends_the_utc_instant_as_iso_text(monkeypatch: pytest.MonkeyPatch) -> None:
    charger, updater = _known(monkeypatch)
    instant = int(datetime(2026, 10, 16, 19, 30, 5, tzinfo=timezone.utc).timestamp())

    assert _send(updater, "systemTime", lambda: instant) is True

    assert _inputs(charger) == [{"timeSettings": {"systemTime": "2026-10-16T19:30:05Z"}}]


def test_the_power_adaptive_mode_reve_does_not_have_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    charger, updater = _known(monkeypatch)

    assert _send(updater, "aiMode", 3) is False

    assert charger.calls == []


@pytest.mark.parametrize(
    ("command", "value"),
    [("aiMode", -1), ("aiVoltage", 215.5), ("minVoltage", -10), ("timeZone", 3.5), ("oneCharge", 2)],
)
def test_impossible_values_are_refused_without_a_request(
    monkeypatch: pytest.MonkeyPatch, command: str, value: Any
) -> None:
    charger, updater = _known(monkeypatch)

    assert _send(updater, command, value) is False

    assert charger.calls == []


# ---------------------------------------------------------------------------
# Session limits: on while they have a value, off when null
# ---------------------------------------------------------------------------

# (number command, its switch, GraphQL session field, a written value, the
# value the next poll reads back in /main units)
_LIMITS = [
    ("energyLimit", "energyLimitS", "energyLimit", 5000, 5.0),
    ("timeLimit", "timeLimitS", "timeLimit", 3600, 3600),
    ("moneyLimit", "moneyLimitS", "costLimit", 50, 50),
]


@pytest.mark.parametrize(("command", "switch", "field", "value", "read"), _LIMITS)
def test_a_limit_switched_on_writes_its_value(
    monkeypatch: pytest.MonkeyPatch, command: str, switch: str, field: str, value: Any, read: Any
) -> None:
    charger, updater = _known(monkeypatch)
    assert _send(updater, command, value) is True  # off: kept, not written
    assert charger.calls == []

    assert _send(updater, switch, 1) is True

    assert _inputs(charger) == [{"session": {field: value}}]
    data = asyncio.run(updater._async_update_data())
    assert data[switch] == 1
    assert data[command] == read


@pytest.mark.parametrize(("command", "switch", "field", "value", "read"), _LIMITS)
def test_a_limit_value_changed_while_on_is_written(
    monkeypatch: pytest.MonkeyPatch, command: str, switch: str, field: str, value: Any, read: Any
) -> None:
    charger, updater = _known(monkeypatch)
    _send(updater, command, value)
    _send(updater, switch, 1)
    asyncio.run(updater._async_update_data())
    charger.calls.clear()

    assert _send(updater, command, value * 2) is True

    assert _inputs(charger) == [{"session": {field: value * 2}}]


@pytest.mark.parametrize(("command", "switch", "field", "value", "read"), _LIMITS)
def test_a_limit_switched_off_is_null_and_its_value_still_shows(
    monkeypatch: pytest.MonkeyPatch, command: str, switch: str, field: str, value: Any, read: Any
) -> None:
    charger, updater = _known(monkeypatch)
    _send(updater, command, value)
    _send(updater, switch, 1)
    asyncio.run(updater._async_update_data())
    charger.calls.clear()

    assert _send(updater, switch, 0) is True

    assert _inputs(charger) == [{"session": {field: None}}]
    data = asyncio.run(updater._async_update_data())
    assert data[switch] == 0
    # The number keeps showing the limit it would switch back on with.
    assert data[command] == read


@pytest.mark.parametrize(("command", "switch", "field", "value", "read"), _LIMITS)
def test_a_limit_reve_refuses_to_clear_but_drops_counts_as_switched_off(
    monkeypatch: pytest.MonkeyPatch, command: str, switch: str, field: str, value: Any, read: Any
) -> None:
    # Measured on hardware 16.10: null answers success:false, the limit goes.
    monkeypatch.setattr(reve.asyncio, "sleep", _no_sleep)
    charger, updater = _known(monkeypatch, _NullRefusingCharger(_reve_main()))
    _send(updater, command, value)
    _send(updater, switch, 1)
    asyncio.run(updater._async_update_data())

    assert _send(updater, switch, 0) is True

    assert asyncio.run(updater._async_update_data())[switch] == 0


@pytest.mark.parametrize(("command", "switch", "field", "value", "read"), _LIMITS)
def test_a_limit_that_stays_after_a_refused_clear_is_a_failed_command(
    monkeypatch: pytest.MonkeyPatch, command: str, switch: str, field: str, value: Any, read: Any
) -> None:
    monkeypatch.setattr(reve.asyncio, "sleep", _no_sleep)
    charger = _NullRefusingCharger(_reve_main())
    charger.drops_limit = False
    charger, updater = _known(monkeypatch, charger)
    _send(updater, command, value)
    _send(updater, switch, 1)
    asyncio.run(updater._async_update_data())

    assert _send(updater, switch, 0) is False


@pytest.mark.parametrize(("command", "switch", "field", "value", "read"), _LIMITS)
def test_a_value_kept_while_off_is_shown_until_switched_on(
    monkeypatch: pytest.MonkeyPatch, command: str, switch: str, field: str, value: Any, read: Any
) -> None:
    charger, updater = _known(monkeypatch)

    _send(updater, command, value)
    data = asyncio.run(updater._async_update_data())

    assert data[switch] == 0
    assert data[command] == read


@pytest.mark.parametrize(("command", "switch", "field", "value", "read"), _LIMITS)
def test_switching_on_a_limit_without_a_value_is_refused(
    monkeypatch: pytest.MonkeyPatch, command: str, switch: str, field: str, value: Any, read: Any
) -> None:
    # /main reports 0 for every limit here: a zero limit would stop at once.
    charger, updater = _known(monkeypatch)

    assert _send(updater, switch, 1) is False

    assert charger.calls == []


def test_a_limit_set_in_the_app_is_switched_back_on_with_its_own_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    charger, updater = _known(monkeypatch)
    charger.station["session"]["energyLimit"] = 7000  # set in the vendor app
    asyncio.run(updater._async_update_data())
    _send(updater, "energyLimitS", 0)
    charger.calls.clear()

    assert _send(updater, "energyLimitS", 1) is True

    assert _inputs(charger) == [{"session": {"energyLimit": 7000}}]


# ---------------------------------------------------------------------------
# What stays on /pageEvent, and classic firmware
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("command", "value"), [
    ("suspendLimits", 1), ("ocppEnabled", 1),
])
def test_undecided_commands_stay_on_page_event(
    monkeypatch: pytest.MonkeyPatch, command: str, value: Any
) -> None:
    charger, updater = _known(monkeypatch)

    assert _send(updater, command, value) is True

    assert charger.paths() == ["/pageEvent"]


@pytest.mark.parametrize(("command", "value"), [
    ("oneCharge", 1), ("aiMode", 3), ("energyLimit", 5000), ("energyLimitS", 1),
    ("timeZone", 3), ("groundCtrl", 0), ("systemTime", 1792026414), ("rstEM1", 0), ("rstEM2", 0),
])
def test_classic_firmware_writes_every_setting_through_page_event(
    monkeypatch: pytest.MonkeyPatch, command: str, value: Any
) -> None:
    from test_reve_firmware import _classic_main

    charger = _Charger(_classic_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    charger.calls.clear()

    assert _send(updater, command, value) is True

    assert charger.paths() == ["/pageEvent"]


def test_every_command_an_entity_sends_has_a_decided_route() -> None:
    api = reve.ReveApi(object())
    routed = {
        "currentSet", "evseEnabled", "oneCharge", "aiMode", "aiVoltage", "minVoltage",
        "timeZone", "systemTime", "groundCtrl", "rstEM1", "rstEM2",
        "energyLimit", "energyLimitS", "timeLimit", "timeLimitS", "moneyLimit", "moneyLimitS",
    }
    for command in routed:
        assert api.route(command) is not None, command
    for command in reve.PAGE_EVENT_COMMANDS:
        assert api.route(command) is None, command
    assert reve.PAGE_EVENT_COMMANDS == frozenset({"suspendLimits", "ocppEnabled"})
