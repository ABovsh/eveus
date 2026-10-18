"""One missed poll: retry quickly instead of racing the grace window.

A charger that misses a single /main reply used to be asked again 59-62 s later
(HA schedules a poll at a whole second plus a per-coordinator offset) while the
60 s grace window closed at 60.5 s, so every miss published a zero-length
`unavailable` row on every entity, and the 30 s control grace always expired.
Seen on REVE (one miss about every 40 minutes, 2026-10-15), but the race is in
the cadence, not the firmware.
"""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from conftest import StreamReaderStub, TEST_HOST, TEST_PASSWORD, TEST_USERNAME
from custom_components.eveus import common_network
from custom_components.eveus.common_network import EveusUpdater
from custom_components.eveus.const import IDLE_UPDATE_INTERVAL

_MAIN = json.loads((Path(__file__).parent / "fixtures" / "real_main_response.json").read_text())
# The idle cadence: the fixture's charger is in standby.
_IDLE = timedelta(seconds=IDLE_UPDATE_INTERVAL)


class _Hass:
    loop = None


class _Response:
    status = 200

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = json.dumps(body).encode()

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    @property
    def content_length(self) -> int:
        return len(self._body)

    @property
    def content(self) -> StreamReaderStub:
        return StreamReaderStub(self._body)


class _Charger:
    """A classic charger whose /main can be made to miss replies."""

    def __init__(self) -> None:
        self.missing = False

    def post(self, url: str, **_kwargs: Any) -> _Response:
        if self.missing:
            raise asyncio.TimeoutError()
        return _Response(dict(_MAIN))


def _updater(monkeypatch: pytest.MonkeyPatch, charger: _Charger) -> EveusUpdater:
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: charger)
    return EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())


def _miss(updater: EveusUpdater) -> None:
    with pytest.raises(Exception):
        asyncio.run(updater._async_update_data())


def test_one_missed_poll_is_retried_after_fifteen_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    from custom_components.eveus.const import FIRST_FAILURE_RETRY_INTERVAL

    charger = _Charger()
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    assert updater.update_interval == _IDLE

    charger.missing = True
    _miss(updater)
    assert updater.update_interval == timedelta(seconds=FIRST_FAILURE_RETRY_INTERVAL)
    assert FIRST_FAILURE_RETRY_INTERVAL < 30  # inside the control grace too

    # A second miss is an outage, not a blip: back to the ordinary cadence.
    _miss(updater)
    assert updater.update_interval == _IDLE

    charger.missing = False
    asyncio.run(updater._async_update_data())
    assert updater.update_interval == _IDLE


def test_a_miss_answered_by_the_quick_retry_returns_to_the_ordinary_cadence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    charger = _Charger()
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    charger.missing = True
    _miss(updater)
    charger.missing = False
    asyncio.run(updater._async_update_data())
    assert updater.update_interval == _IDLE


def test_a_long_outage_keeps_the_ordinary_cadence(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger()
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    charger.missing = True
    for _ in range(4):
        _miss(updater)
        updater._next_poll_attempt = 0.0
    assert updater.update_interval == _IDLE
