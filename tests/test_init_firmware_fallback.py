"""Firmware-version fallback via /init for GitHub issue #11 (firmware 1.x).

Firmware 1.x drops `verFWMain` (and `firmware`) from /main entirely, so
sw_version would stay "Unknown" forever. /init exposes ESP_SW_version /
MCU_SW_version as an integer (e.g. 151), which this fallback fetches once
and formats as "1.51".
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
from custom_components.eveus import common_network
from custom_components.eveus.common_network import EveusUpdater
from custom_components.eveus.utils import get_device_info

FW151_MAIN = json.loads(
    (Path(__file__).parent / "fixtures" / "fw151_unknown_state_main.json").read_text()
)
FW151_INIT = json.loads(
    (Path(__file__).parent / "fixtures" / "fw151_init.json").read_text()
)

REAL_MAIN = json.loads(
    (Path(__file__).parent / "fixtures" / "real_main_response.json").read_text()
)


class _Response:
    def __init__(self, payload: object, *, status: int = 200, raise_error: Exception | None = None) -> None:
        self.status = status
        self.payload = payload
        self._raise_error = raise_error

    async def __aenter__(self) -> "_Response":
        if self._raise_error is not None:
            raise self._raise_error
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    @property
    def content_length(self) -> int | None:
        return len(json.dumps(self.payload).encode())

    @property
    def content(self) -> "_StreamReader":
        return _StreamReader(json.dumps(self.payload).encode())


class _NonJSONResponse(_Response):
    @property
    def content_length(self) -> int | None:
        return len(b"not json")

    @property
    def content(self) -> "_StreamReader":
        return _StreamReader(b"not json")


class _StreamReader:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    async def iter_chunked(self, size: int):
        for i in range(0, len(self._raw), size):
            yield self._raw[i : i + size]


class _MultiSession:
    """Dispatches /main and /init responses independently, tracking call counts."""

    def __init__(self, main_response: _Response, init_response: _Response | None = None) -> None:
        self.main_response = main_response
        self.init_response = init_response
        self.calls: list[str] = []

    def post(self, url: str, **kwargs: object) -> _Response:
        self.calls.append(url)
        if url.endswith("/init"):
            assert self.init_response is not None, "unexpected /init call"
            return self.init_response
        return self.main_response

    @property
    def init_call_count(self) -> int:
        return sum(1 for url in self.calls if url.endswith("/init"))


class _Hass:
    loop = None


def _make_updater(session: _MultiSession) -> EveusUpdater:
    return EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())


async def _poll_and_settle(updater: EveusUpdater) -> dict:
    """Mimic async_setup_entry: a /main poll, then the /init fallback check.

    Mirrors the production hook order in async_setup_entry (first refresh,
    then async_maybe_fetch_init_firmware) rather than the coordinator's
    internal poll loop, which never triggers the fallback itself.
    """
    data = await updater._async_update_data()
    updater.data = data
    await updater.async_maybe_fetch_init_firmware()
    return data


# ---------------------------------------------------------------------------
# Modern firmware: verFWMain present -> /init must never be requested.
# ---------------------------------------------------------------------------


def test_init_not_fetched_when_verfwmain_present(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _MultiSession(_Response(REAL_MAIN))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = _make_updater(session)

    asyncio.run(_poll_and_settle(updater))
    # Run a second poll too -- still must never touch /init.
    asyncio.run(_poll_and_settle(updater))

    assert session.init_call_count == 0
    assert getattr(updater, "_init_fw_fallback", None) is None


# ---------------------------------------------------------------------------
# Firmware 1.x: verFWMain absent -> /init fetched once, cached, formatted.
# ---------------------------------------------------------------------------


def test_init_fetched_and_cached_for_fw151(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _MultiSession(_Response(FW151_MAIN), _Response(FW151_INIT))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = _make_updater(session)

    asyncio.run(_poll_and_settle(updater))
    assert session.init_call_count == 1
    assert updater._init_fw_fallback == "1.51"

    # Second poll: fallback already cached, /init must not be requested again.
    asyncio.run(_poll_and_settle(updater))
    assert session.init_call_count == 1
    assert updater._init_fw_fallback == "1.51"


def test_device_info_uses_init_fallback_firmware() -> None:
    info = get_device_info(TEST_HOST, FW151_MAIN, init_fw_fallback="1.51")
    assert info["sw_version"] == "1.51"


def test_device_info_ignores_fallback_when_verfwmain_present() -> None:
    info = get_device_info(TEST_HOST, REAL_MAIN, init_fw_fallback="1.51")
    assert info["sw_version"] != "1.51"


# ---------------------------------------------------------------------------
# /init failure modes must all degrade to "Unknown" and never fail the poll.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "init_response",
    [
        _Response({}, status=500, raise_error=common_network.aiohttp.ClientResponseError(
            request_info=None, history=(), status=500
        )),
        _Response({}, raise_error=asyncio.TimeoutError()),
        _NonJSONResponse("not json"),
        _Response({"unrelated_field": 1}),
        _Response({"ESP_SW_version": "not-an-int"}),
    ],
)
def test_init_failure_modes_degrade_to_unknown(
    monkeypatch: pytest.MonkeyPatch, init_response: _Response
) -> None:
    session = _MultiSession(_Response(FW151_MAIN), init_response)
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = _make_updater(session)

    data = asyncio.run(_poll_and_settle(updater))

    assert data is not None
    assert updater._init_fw_fallback is None
    info = get_device_info(TEST_HOST, FW151_MAIN, init_fw_fallback=updater._init_fw_fallback)
    assert info["sw_version"] == "Unknown"


def test_init_fallback_uses_mcu_sw_version_when_esp_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _MultiSession(
        _Response(FW151_MAIN), _Response({"MCU_SW_version": 151})
    )
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = _make_updater(session)
    asyncio.run(_poll_and_settle(updater))
    assert updater._init_fw_fallback == "1.51"
