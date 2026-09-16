"""Shared real-Home-Assistant test fixtures: only the charger's HTTP mocked.

`aioclient_mock` is a pytest fixture, picked up automatically by every test
file in this directory. The rest are plain helpers other files import.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.syrupy import HomeAssistantSnapshotExtension
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.eveus import common_network
from custom_components.eveus.const import (
    CONF_MODEL,
    CONF_PHASES,
    CONF_SCHEME,
    CONF_SOC_MODE,
    DOMAIN,
    MODEL_16A,
    SOC_MODE_BASIC,
)

_REAL_MAIN = json.loads(
    (Path(__file__).parent.parent / "tests" / "fixtures" / "real_main_response.json")
    .read_text(encoding="utf-8")
)


class _MockedSession:
    """Routes the integration's POSTs to the mocker without a real ClientSession.

    The `aioclient_mock` fixture builds a real session whose DNS resolver,
    closed at teardown on older Home Assistant releases, leaves a shutdown
    thread behind that the plugin's cleanup check rejects.
    """

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        self._mocker = mocker

    def post(self, url: str, **kwargs: object) -> "_MockedRequest":
        return _MockedRequest(self._mocker.match_request("post", url, **kwargs))


class _MockedRequest:
    def __init__(self, pending) -> None:
        self._pending = pending

    async def __aenter__(self) -> AiohttpClientMockResponse:
        return await self._pending

    async def __aexit__(self, *exc_info: object) -> None:
        return None


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Snapshot assertion using the Home Assistant serializer (State, etc.)."""
    return snapshot.use_extension(HomeAssistantSnapshotExtension)


@pytest.fixture
def aioclient_mock(monkeypatch: pytest.MonkeyPatch) -> AiohttpClientMocker:
    mocker = AiohttpClientMocker()
    session = _MockedSession(mocker)
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    return mocker


def _entry(hass: HomeAssistant, host: str) -> MockConfigEntry:
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=host,
        data={
            CONF_HOST: host,
            CONF_USERNAME: "test_user",  # NOSONAR(python:S2068) - test fixture
            CONF_PASSWORD: "test_password",  # NOSONAR(python:S2068) - test fixture
            CONF_MODEL: MODEL_16A,
            CONF_SCHEME: "http",
            CONF_PHASES: 1,
            CONF_SOC_MODE: SOC_MODE_BASIC,
        },
        options={},
    )
    config_entry.add_to_hass(hass)
    return config_entry


def _mock_charger(aioclient_mock: AiohttpClientMocker, host: str, **main: object) -> None:
    aioclient_mock.post(f"http://{host}/main", **(main or {"json": _REAL_MAIN}))
    aioclient_mock.post(f"http://{host}/pageEvent", text="ok")


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
