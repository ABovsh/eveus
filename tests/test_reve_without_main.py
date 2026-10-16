"""REVE without ``/main``: setup and polling once the vendor removes it.

The vendor calls ``/main`` and ``/pageEvent`` leftovers of the move to GraphQL.
When ``/main`` answers with an HTTP error or with something that is not JSON
while the charger is plainly there, the local GraphQL ``stationData`` is asked
instead, and a REVE reply (``wifiFWVersion`` REVE_…) sets the charger up and
polls it. A charger that does not answer at all (switched off, unreachable)
costs no extra request: GraphQL is not tried on a timeout or a refused
connection, and never on a 401.
"""
from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

import aiohttp
import pytest

from conftest import TEST_HOST
from custom_components.eveus import config_flow, reve
from custom_components.eveus.common_network import EveusUnreachable
from custom_components.eveus.config_flow import CannotConnect, InvalidAuth, InvalidResponse, validate_input
from test_config_flow import _Response as _FlowResponse
from test_config_flow import _input
from test_reve_firmware import _Charger, _load, _reve_main, _Response, _updater

_STATION = _load("reve_graphql_station.json")


# ---------------------------------------------------------------------------
# Setup (config flow)
# ---------------------------------------------------------------------------


class _RoutedSession:
    """Answers each path with its own response; records the paths asked."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.paths: list[str] = []

    def post(self, url: str, **kwargs: Any) -> Any:
        path = "/" + url.split("/", 3)[3]
        self.paths.append(path)
        route = self.routes[path]
        if isinstance(route, Exception):
            raise route
        return route


class _Hass:
    def __init__(self, session: _RoutedSession) -> None:
        self.session = session


@pytest.fixture(autouse=True)
def _patch_clientsession(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_flow.aiohttp_client, "async_get_clientsession", lambda hass: hass.session)


def _http_error(status: int) -> _FlowResponse:
    return _FlowResponse(
        status=status,
        raise_status=aiohttp.ClientResponseError(request_info=None, history=(), status=status),
    )


def _graphql(body: Any = None) -> _FlowResponse:
    return _FlowResponse(payload=copy.deepcopy(_STATION) if body is None else body)


@pytest.mark.parametrize("main", [
    pytest.param(lambda: _http_error(404), id="main-404"),
    pytest.param(lambda: _FlowResponse(payload="<html>Not here</html>"), id="main-not-json"),
])
def test_a_reve_charger_without_main_is_set_up_through_graphql(main: Any) -> None:
    session = _RoutedSession({"/main": main(), "/graphql": _graphql()})

    result = asyncio.run(validate_input(_Hass(session), _input()))

    assert session.paths == ["/main", "/graphql"]
    assert result["data"]["host"] == TEST_HOST
    assert result["device_info"]["current_set"] == 7
    assert "2.4.0" in result["device_info"]["firmware"]


def test_the_graphql_setup_read_is_the_measurements_document(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    session = _RoutedSession({"/main": _http_error(404), "/graphql": _graphql()})
    real_post = session.post

    def record(url: str, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return real_post(url, **kwargs)

    session.post = record
    asyncio.run(validate_input(_Hass(session), _input()))

    body = json.loads(seen[1]["data"])
    assert body["query"] == reve.MEASUREMENTS_QUERY
    assert body["operationName"] == "StationData"


def test_main_missing_and_graphql_not_reve_keeps_the_main_error() -> None:
    station = copy.deepcopy(_STATION)
    station["data"]["stationData"]["wifiFWVersion"] = "SOMETHING_ELSE"
    session = _RoutedSession({"/main": _http_error(404), "/graphql": _graphql(station)})

    with pytest.raises(CannotConnect):
        asyncio.run(validate_input(_Hass(session), _input()))


def test_main_not_json_and_graphql_silent_keeps_the_main_error() -> None:
    session = _RoutedSession({"/main": _FlowResponse(payload="<html>Login</html>"), "/graphql": _http_error(500)})

    with pytest.raises(InvalidResponse):
        asyncio.run(validate_input(_Hass(session), _input()))


def test_wrong_credentials_never_try_graphql() -> None:
    session = _RoutedSession({"/main": _FlowResponse(status=401), "/graphql": _graphql()})

    with pytest.raises(InvalidAuth):
        asyncio.run(validate_input(_Hass(session), _input()))

    assert session.paths == ["/main"]


def test_an_unreachable_charger_never_tries_graphql() -> None:
    session = _RoutedSession({"/main": asyncio.TimeoutError(), "/graphql": _graphql()})

    with pytest.raises(CannotConnect):
        asyncio.run(validate_input(_Hass(session), _input()))

    assert session.paths == ["/main"]


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------


class _NoMainCharger(_Charger):
    """REVE after the vendor removed /main: 404 there, GraphQL as before."""

    def __init__(self) -> None:
        super().__init__(_reve_main())
        self.main_status = 404


def test_the_first_poll_without_main_recognises_reve_through_graphql(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _NoMainCharger()
    updater = _updater(monkeypatch, charger)

    data = asyncio.run(updater._async_update_data())

    assert charger.paths()[:2] == ["/main", "/graphql"]
    assert updater.firmware_family == "reve"
    assert updater.last_update_success is True
    assert data["state"] == 2
    assert data["sh1Start"] == 1380  # the settings read follows in the same poll
    assert updater.last_poll_source == "graphql"


def test_a_charger_without_main_keeps_polling_through_graphql(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _NoMainCharger()
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    charger.calls.clear()

    data = asyncio.run(updater._async_update_data())

    assert charger.paths() == ["/graphql"]
    assert data["state"] == 2


def test_an_unreachable_charger_costs_no_graphql_request(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    real_post = charger.post

    def offline(url: str, **kwargs: Any) -> _Response:
        if url.endswith("/main"):
            raise aiohttp.ClientConnectionError()
        return real_post(url, **kwargs)

    charger.post = offline
    updater = _updater(monkeypatch, charger)

    with pytest.raises(EveusUnreachable):
        asyncio.run(updater._async_update_data())

    assert "/graphql" not in charger.paths()


def test_a_main_error_from_a_charger_that_is_not_reve_stays_a_failed_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _NoMainCharger()
    charger.measurements_status = 404  # no GraphQL either: not REVE
    updater = _updater(monkeypatch, charger)

    with pytest.raises(EveusUnreachable):
        asyncio.run(updater._async_update_data())

    assert updater.firmware_family == "classic"
