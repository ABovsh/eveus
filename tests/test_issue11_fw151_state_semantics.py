"""Follow-up regression tests for GitHub issue #11 (firmware 1.x state semantics).

The first round of fixes made setup succeed on firmware 1.x (MCU_SW_version
151), but the reporter's retest surfaced two residual defects:

1. The State sensor is a closed-options ENUM (since 4.18.0); the unmapped-state
   fallback rendered "Unknown (20)", which is not in the options list, so Home
   Assistant rejected every state write and the sensor stayed `unknown`.
2. Firmware 1.x does not just add extra codes — it re-uses code 3 for *active
   charging* (modern firmware: 3 = connected-idle, 4 = charging) and 20 for
   idle/standby. Without translation, a 1.x charger mid-session reads
   "Connected", never gets the fast poll cadence, and never fires the
   charging-started/finished events.

Firmware 1.x payloads are recognizable by the complete absence of the
verFWMain/firmware fields; modern payloads always carry verFWMain and must
pass through byte-for-byte unchanged.
"""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path

import pytest

from conftest import EveusTestUpdater, TEST_HOST, TEST_PASSWORD, TEST_USERNAME
from custom_components.eveus import common_network, sensor_definitions as sd
from custom_components.eveus.common_network import EveusUpdater
from custom_components.eveus.const import (
    CHARGING_STATES,
    CHARGING_UPDATE_INTERVAL,
    DEVICE_STATE_CHARGING,
    DEVICE_STATE_STANDBY,
    IDLE_UPDATE_INTERVAL,
    LEGACY_RAW_STATE_KEY,
)
from custom_components.eveus.sensor_definitions import create_sensor_specifications

_FIXTURES = Path(__file__).parent / "fixtures"
FW151_MAIN = json.loads((_FIXTURES / "fw151_unknown_state_main.json").read_text())
REAL_MAIN = json.loads((_FIXTURES / "real_main_response.json").read_text())


def _spec(key: str):
    return next(s for s in create_sensor_specifications() if s.key == key)


def _fw151_payload(**overrides: object) -> dict:
    payload = dict(FW151_MAIN)
    payload.update(overrides)
    return payload


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

    def post(self, url: str, **kwargs: object) -> _Response:
        return self.response


class _Hass:
    loop = None


def _poll(payload: dict, monkeypatch: pytest.MonkeyPatch) -> tuple[EveusUpdater, dict]:
    session = _Session(_Response(payload))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    data = asyncio.run(updater._async_update_data())
    return updater, data


# ---------------------------------------------------------------------------
# 1. The State sensor value must ALWAYS be a member of its enum options list —
#    Home Assistant raises ValueError on any other write, leaving the sensor
#    stuck at `unknown` (the exact symptom from the issue-#11 retest).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", [8, 20, 42, 255])
def test_unmapped_state_renders_a_valid_enum_option(state: int) -> None:
    updater = EveusTestUpdater({"state": state})
    value = sd.get_charger_state(updater, None)
    assert value in _spec("state").options


@pytest.mark.parametrize("state", sorted(CHARGING_STATES))
def test_mapped_states_render_their_named_option(state: int) -> None:
    updater = EveusTestUpdater({"state": state})
    value = sd.get_charger_state(updater, None)
    assert value == CHARGING_STATES[state]
    assert value in _spec("state").options


def test_state_sensor_exposes_raw_code_attribute_for_unmapped_state() -> None:
    attributes_fn = _spec("state").attributes_fn
    assert attributes_fn is not None
    updater = EveusTestUpdater({"state": 20})
    assert attributes_fn(updater, None) == {"raw_state": 20}


def test_state_sensor_exposes_raw_code_attribute_for_translated_state() -> None:
    attributes_fn = _spec("state").attributes_fn
    updater = EveusTestUpdater(
        {"state": DEVICE_STATE_STANDBY, LEGACY_RAW_STATE_KEY: 20}
    )
    assert attributes_fn(updater, None) == {"raw_state": 20}


def test_state_sensor_has_no_raw_attribute_for_plain_mapped_state() -> None:
    attributes_fn = _spec("state").attributes_fn
    updater = EveusTestUpdater({"state": 4})
    assert attributes_fn(updater, None) == {}


# ---------------------------------------------------------------------------
# 2. Legacy state translation on coordinator polls (firmware 1.x payloads
#    only: no verFWMain/firmware field).
# ---------------------------------------------------------------------------


def test_poll_translates_legacy_idle_state_to_standby(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, data = _poll(_fw151_payload(), monkeypatch)
    assert data["state"] == DEVICE_STATE_STANDBY
    assert data[LEGACY_RAW_STATE_KEY] == 20


def test_poll_translates_legacy_charging_state_to_charging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _fw151_payload(state=3, powerMeas=1590, curMeas1=7.3)
    _, data = _poll(payload, monkeypatch)
    assert data["state"] == DEVICE_STATE_CHARGING
    assert data[LEGACY_RAW_STATE_KEY] == 3


def test_poll_keeps_legacy_state3_as_connected_without_power(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _fw151_payload(state=3, powerMeas=0, curMeas1=0)
    _, data = _poll(payload, monkeypatch)
    assert data["state"] == 3
    assert LEGACY_RAW_STATE_KEY not in data


def test_poll_leaves_other_legacy_states_untranslated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _fw151_payload(state=42)
    _, data = _poll(payload, monkeypatch)
    assert data["state"] == 42
    assert LEGACY_RAW_STATE_KEY not in data


# ---------------------------------------------------------------------------
# 3. Adaptive polling + charging events now follow the translated codes.
# ---------------------------------------------------------------------------


def test_legacy_idle_uses_idle_cadence_not_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater, _ = _poll(_fw151_payload(powerMeas=0, curMeas1=0), monkeypatch)
    assert updater.update_interval == timedelta(seconds=IDLE_UPDATE_INTERVAL)


def test_legacy_charging_uses_fast_cadence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater, _ = _poll(
        _fw151_payload(state=3, powerMeas=1590, curMeas1=7.3), monkeypatch
    )
    assert updater.update_interval == timedelta(seconds=CHARGING_UPDATE_INTERVAL)


# ---------------------------------------------------------------------------
# 4. Modern-firmware regression pinning: payloads carrying verFWMain must
#    never be translated, whatever the state/power combination.
# ---------------------------------------------------------------------------


def test_modern_payload_is_never_translated(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = dict(REAL_MAIN)
    assert payload.get("verFWMain")  # fixture sanity: modern signature present
    original_state = payload["state"]
    _, data = _poll(payload, monkeypatch)
    assert data["state"] == original_state
    assert LEGACY_RAW_STATE_KEY not in data


def test_modern_connected_state_with_power_stays_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = dict(REAL_MAIN)
    payload.update({"state": 3, "powerMeas": 1590, "curMeas1": 7.3})
    _, data = _poll(payload, monkeypatch)
    assert data["state"] == 3
    assert LEGACY_RAW_STATE_KEY not in data


def test_modern_state_map_is_unchanged() -> None:
    assert CHARGING_STATES == {
        0: "Startup",
        1: "System Test",
        2: "Standby",
        3: "Connected",
        4: "Charging",
        5: "Charge Complete",
        6: "Paused",
        7: "Error",
    }
