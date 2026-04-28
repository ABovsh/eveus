"""Unit tests for the Eveus data coordinator."""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.eveus import common_network
from custom_components.eveus.common_network import EveusUpdater
from custom_components.eveus.const import CHARGING_UPDATE_INTERVAL, IDLE_UPDATE_INTERVAL


class _Hass:
    """Minimal hass object for coordinator construction."""

    loop = None


class _Response:
    def __init__(self, *, status: int = 200, payload: object | None = None) -> None:
        self.status = status
        self.payload = payload if payload is not None else {"state": 2}

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def text(self) -> str:
        if isinstance(self.payload, str):
            return self.payload
        return json.dumps(self.payload)


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, **kwargs: object) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return self.response


@pytest.fixture
def coordinator(monkeypatch: pytest.MonkeyPatch) -> tuple[EveusUpdater, _Session]:
    """Create a coordinator with a fake HTTP session."""
    session = _Session(_Response(payload={"state": 4, "powerMeas": 7200}))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    return EveusUpdater("192.168.1.50", "admin", "secret", _Hass()), session


def test_update_data_fetches_payload_and_sets_charging_interval(
    coordinator: tuple[EveusUpdater, _Session],
) -> None:
    updater, session = coordinator

    data = asyncio.run(updater._async_update_data())

    assert data == {"state": 4, "powerMeas": 7200}
    assert session.calls[0]["url"] == "http://192.168.1.50/main"
    assert updater.update_interval == timedelta(seconds=CHARGING_UPDATE_INTERVAL)
    assert updater.connection_quality["consecutive_failures"] == 0


def test_update_data_sets_idle_interval_when_device_is_not_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(_Response(payload={"state": 2, "powerMeas": 0}))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater("192.168.1.50", "admin", "secret", _Hass())

    asyncio.run(updater._async_update_data())

    assert updater.update_interval == timedelta(seconds=IDLE_UPDATE_INTERVAL)


def test_update_data_raises_auth_failed_on_unauthorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(_Response(status=401))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater("192.168.1.50", "admin", "secret", _Hass())

    with pytest.raises(ConfigEntryAuthFailed):
        asyncio.run(updater._async_update_data())


def test_update_data_raises_update_failed_for_bad_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(_Response(payload="{not-json"))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater("192.168.1.50", "admin", "secret", _Hass())

    with pytest.raises(UpdateFailed):
        asyncio.run(updater._async_update_data())

    assert updater.connection_quality["consecutive_failures"] == 1
    assert updater.connection_quality["last_error"] == "JSONDecodeError"

