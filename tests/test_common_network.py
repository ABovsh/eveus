"""Unit tests for the Eveus data coordinator."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.eveus import common_network
from custom_components.eveus.common_network import EveusUpdater
from custom_components.eveus.const import (
    CHARGING_UPDATE_INTERVAL,
    RETRY_DELAY,
)


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

    async def json(self, **kwargs: object) -> object:
        if isinstance(self.payload, str):
            return json.loads(self.payload)
        return self.payload


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, **kwargs: object) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return self.response


class _FailingSession:
    def post(self, url: str, **kwargs: object) -> _Response:
        raise asyncio.TimeoutError()


@pytest.fixture
def coordinator(monkeypatch: pytest.MonkeyPatch) -> tuple[EveusUpdater, _Session]:
    """Create a coordinator with a fake HTTP session."""
    session = _Session(_Response(payload={"state": 4, "powerMeas": 7200}))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    return EveusUpdater("192.168.1.50", "admin", "secret", _Hass()), session


def test_update_data_fetches_payload_and_uses_stable_interval(
    coordinator: tuple[EveusUpdater, _Session],
) -> None:
    updater, session = coordinator

    data = asyncio.run(updater._async_update_data())

    assert data == {"state": 4, "powerMeas": 7200}
    assert session.calls[0]["url"] == "http://192.168.1.50/main"
    assert updater.update_interval == timedelta(seconds=CHARGING_UPDATE_INTERVAL)
    assert updater.connection_quality["consecutive_failures"] == 0


def test_coordinator_compatibility_helpers() -> None:
    updater = EveusUpdater("192.168.1.50", "admin", "secret", _Hass())

    assert updater.available is True
    assert asyncio.run(updater.async_shutdown()) is None
    assert updater._should_log() is True
    assert updater._should_log() is False


def test_update_data_keeps_stable_interval_when_device_is_not_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(_Response(payload={"state": 2, "powerMeas": 0}))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater("192.168.1.50", "admin", "secret", _Hass())

    asyncio.run(updater._async_update_data())

    assert updater.update_interval == timedelta(seconds=CHARGING_UPDATE_INTERVAL)


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


def test_update_data_raises_update_failed_for_non_dict_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(_Response(payload=["not", "a", "mapping"]))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater("192.168.1.50", "admin", "secret", _Hass())

    with pytest.raises(UpdateFailed):
        asyncio.run(updater._async_update_data())

    assert updater.connection_quality["consecutive_failures"] == 1
    assert updater.connection_quality["last_error"] == "ValueError"


def test_update_data_marks_unavailable_and_raises_update_failed_on_network_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        common_network,
        "async_get_clientsession",
        lambda hass: _FailingSession(),
    )
    updater = EveusUpdater("192.168.1.50", "admin", "secret", _Hass())
    updater.data = {"state": 2}

    with pytest.raises(UpdateFailed):
        asyncio.run(updater._async_update_data())

    assert updater.available is False
    assert updater.connection_quality["last_error"] == "TimeoutError"


def test_initial_network_failure_returns_empty_payload_to_allow_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        common_network,
        "async_get_clientsession",
        lambda hass: _FailingSession(),
    )
    updater = EveusUpdater("192.168.1.50", "admin", "secret", _Hass())

    assert asyncio.run(updater._async_update_data()) == {}
    assert updater.available is False


def test_send_command_refreshes_data_only_after_success() -> None:
    updater = EveusUpdater("192.168.1.50", "admin", "secret", _Hass())
    refreshes = 0

    async def request_refresh() -> None:
        nonlocal refreshes
        refreshes += 1

    async def successful_command(command: str, value: object) -> bool:
        return True

    async def failed_command(command: str, value: object) -> bool:
        return False

    updater.async_request_refresh = request_refresh
    updater._command_manager = SimpleNamespace(send_command=successful_command)

    assert asyncio.run(updater.send_command("currentSet", 16)) is True
    assert refreshes == 1

    updater._command_manager = SimpleNamespace(send_command=failed_command)

    assert asyncio.run(updater.send_command("currentSet", 12)) is False
    assert refreshes == 1


def test_failure_recording_reduces_polling_when_device_appears_offline() -> None:
    updater = EveusUpdater("192.168.1.50", "admin", "secret", _Hass())
    updater._last_success_time = time.time() - 700
    updater._consecutive_failures = 10

    updater._record_failure(asyncio.TimeoutError())

    assert updater.is_likely_offline is True
    assert updater._next_poll_attempt > time.time()
    assert updater.connection_quality["last_error"] == "TimeoutError"


def test_failure_recording_enters_silent_mode_after_many_failures() -> None:
    updater = EveusUpdater("192.168.1.50", "admin", "secret", _Hass())
    updater._consecutive_failures = 20

    updater._record_failure(asyncio.TimeoutError())

    assert updater._silent_mode is True


def test_connection_quality_uses_recent_poll_window() -> None:
    updater = EveusUpdater("192.168.1.50", "admin", "secret", _Hass())
    updater._success_count = 100
    updater._total_count = 100

    for _ in range(10):
        updater._poll_results.append(False)

    metrics = updater.connection_quality

    assert metrics["success_rate"] == 0
    assert metrics["sample_count"] == 10


def test_offline_failure_recording_is_quiet_at_normal_log_levels(
    caplog: pytest.LogCaptureFixture,
) -> None:
    updater = EveusUpdater("192.168.1.50", "admin", "secret", _Hass())
    updater._last_success_time = time.time() - 700
    updater._consecutive_failures = 10

    with caplog.at_level(logging.INFO, logger="custom_components.eveus.common_network"):
        updater._record_failure(asyncio.TimeoutError())

    assert updater.available is False
    assert caplog.records == []
