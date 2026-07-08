"""Regression tests for GitHub issue #11.

Eveus firmware 1.x (MCU_SW_version 151) reports device-state values outside
CHARGING_STATES (observed: 20). The old validator hard-failed any such state,
so the coordinator's first poll raised PayloadError -> UpdateFailed and setup
never completed. The fixture below is a verbatim capture from that firmware's
POST /main.
"""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path

import pytest

from conftest import EveusTestUpdater, TEST_HOST, TEST_PASSWORD, TEST_USERNAME
from custom_components.eveus import common_network, sensor_definitions as sd
from custom_components.eveus._payload import validate_main_payload
from custom_components.eveus.common_network import EveusUpdater
from custom_components.eveus.const import CHARGING_UPDATE_INTERVAL, OFFLINE_UPDATE_INTERVAL

FW151_MAIN = json.loads(
    (Path(__file__).parent / "fixtures" / "fw151_unknown_state_main.json").read_text()
)


# ---------------------------------------------------------------------------
# 1. validate_main_payload accepts the fw-1.x payload unchanged
# ---------------------------------------------------------------------------


def test_validate_main_payload_accepts_fw151_unknown_state() -> None:
    payload = dict(FW151_MAIN)
    assert validate_main_payload(payload) is payload


@pytest.mark.parametrize("state", [0, 255])
def test_validate_main_payload_accepts_state_range_boundaries(state: int) -> None:
    payload = {"state": state, "currentSet": 16}
    assert validate_main_payload(payload)["state"] == state


@pytest.mark.parametrize("state", [-1, 256, 1000])
def test_validate_main_payload_still_rejects_state_outside_byte_range(state: int) -> None:
    from custom_components.eveus._payload import PayloadError

    with pytest.raises(PayloadError):
        validate_main_payload({"state": state, "currentSet": 16})


# ---------------------------------------------------------------------------
# 2. Coordinator poll with fw-1.x payload succeeds (no UpdateFailed)
# ---------------------------------------------------------------------------


class _Response:
    def __init__(self, payload: dict) -> None:
        self.status = 200
        self.payload = payload

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def text(self) -> str:
        return json.dumps(self.payload)

    async def json(self, **kwargs: object) -> object:
        return self.payload

    @property
    def content_length(self) -> int | None:
        return len(json.dumps(self.payload).encode())

    @property
    def content(self) -> "_StreamReader":
        return _StreamReader(json.dumps(self.payload).encode())


class _StreamReader:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    async def iter_chunked(self, size: int):
        for i in range(0, len(self._raw), size):
            yield self._raw[i : i + size]


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, **kwargs: object) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return self.response


class _Hass:
    loop = None


def test_coordinator_poll_with_fw151_payload_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session(_Response(FW151_MAIN))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    data = asyncio.run(updater._async_update_data())

    assert data["state"] == 20
    assert updater.connection_quality["consecutive_failures"] == 0


# ---------------------------------------------------------------------------
# 3. State sensor reads "Unknown (20)"
# ---------------------------------------------------------------------------


def test_state_sensor_reports_unknown_with_value_for_unmapped_state() -> None:
    updater = EveusTestUpdater(FW151_MAIN)
    assert sd.get_charger_state(updater, None) == "Unknown (20)"


@pytest.mark.parametrize("state", [0, 4, 7])
def test_state_sensor_unaffected_for_known_states(state: int) -> None:
    from custom_components.eveus.const import CHARGING_STATES

    updater = EveusTestUpdater({"state": state})
    assert sd.get_charger_state(updater, None) == CHARGING_STATES[state]


# ---------------------------------------------------------------------------
# 4. Charging-detection fallback ONLY for unknown states
# ---------------------------------------------------------------------------


def test_tune_update_interval_unknown_state_with_power_uses_fast_cadence() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._tune_update_interval({"state": 20, "powerMeas": 7200})
    assert updater.update_interval == timedelta(seconds=CHARGING_UPDATE_INTERVAL)


def test_tune_update_interval_unknown_state_with_current_uses_fast_cadence() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._tune_update_interval({"state": 20, "curMeas1": 6.2})
    assert updater.update_interval == timedelta(seconds=CHARGING_UPDATE_INTERVAL)


def test_tune_update_interval_unknown_state_without_power_stays_offline_cadence() -> None:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._tune_update_interval({"state": 20, "powerMeas": 0, "curMeas1": 0})
    assert updater.update_interval == timedelta(seconds=OFFLINE_UPDATE_INTERVAL)


@pytest.mark.parametrize("state", [0, 1, 2, 3, 5, 6, 7])
def test_tune_update_interval_known_states_ignore_power_fallback(state: int) -> None:
    """Known states must follow the pre-existing code path -- the power/current
    fallback branch must be unreachable for them even when powerMeas is high."""
    from custom_components.eveus.const import SESSION_ACTIVE_STATES

    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._tune_update_interval({"state": state, "powerMeas": 7200})
    expected = CHARGING_UPDATE_INTERVAL if state in SESSION_ACTIVE_STATES else 60
    assert updater.update_interval == timedelta(seconds=expected)


def test_coordinator_poll_fw151_with_zero_power_uses_offline_cadence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = dict(FW151_MAIN)
    payload["powerMeas"] = 0
    payload["curMeas1"] = 0
    session = _Session(_Response(payload))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    asyncio.run(updater._async_update_data())

    assert updater.update_interval == timedelta(seconds=OFFLINE_UPDATE_INTERVAL)
