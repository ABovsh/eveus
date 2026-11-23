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
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from conftest import StreamReaderStub, EveusTestUpdater, TEST_HOST, TEST_PASSWORD, TEST_USERNAME
from custom_components.eveus import common_network, sensor_definitions as sd
from custom_components.eveus.common_network import EveusUpdater
from custom_components.eveus.const import (
    CHARGING_STATES,
    CHARGING_UPDATE_INTERVAL,
    DEVICE_STATE_CHARGING,
    DEVICE_STATE_STANDBY,
    EVENT_CHARGING_FINISHED,
    EVENT_CHARGING_STARTED,
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
    def content(self) -> "StreamReaderStub":
        return StreamReaderStub(json.dumps(self.payload).encode())


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


@pytest.mark.parametrize(
    ("raw_state", "state", "substate"),
    [
        (8, 7, 1),
        (9, 7, 5),
        (10, 7, 6),
        (11, 7, 5),
        (12, 7, 10),
        (13, 7, 14),
        (14, 7, 8),
        (15, 5, 3),
        (16, 5, 2),
        (17, 5, 4),
        (18, 5, 5),
        (19, 5, 7),
        (21, 7, 3),
        (22, 6, 10),
        (23, 7, 9),
        (24, 7, 4),
    ],
)
def test_poll_translates_legacy_errors_limits_and_adaptive_pause(
    raw_state: int, state: int, substate: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, data = _poll(_fw151_payload(state=raw_state), monkeypatch)
    assert (data["state"], data.get("subState")) == (state, substate)
    assert data[LEGACY_RAW_STATE_KEY] == raw_state


@pytest.mark.parametrize("substate", [None, 0, 3])
def test_poll_preserves_an_explicit_legacy_substate(
    substate: int | None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater, data = _poll(_fw151_payload(state=16, subState=substate), monkeypatch)
    assert data["state"] == 5
    assert data["subState"] == substate
    assert sd.get_not_charging_reason(updater, None) == "Charge Complete"


@pytest.mark.parametrize(
    ("state", "label", "substate", "reason"),
    [
        (24, "Error", "Current Leak Low", "Error"),
        (22, "Paused", "Paused by Adaptive Mode", "Paused by Adaptive Mode"),
        (30, "Unknown", None, "Unknown"),
    ],
)
def test_legacy_state_sensor_values(
    state: int, label: str, substate: str | None, reason: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater, _ = _poll(_fw151_payload(state=state), monkeypatch)
    assert sd.get_charger_state(updater, None) == label
    assert sd.get_charger_substate(updater, None) == substate
    assert sd.get_not_charging_reason(updater, None) == reason


def test_legacy_leakage_fault_is_named_in_the_reason_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater, _ = _poll(_fw151_payload(state=24), monkeypatch)
    assert sd.get_not_charging_reason_attrs(updater, None) == {"error": "Current Leak Low"}


def test_poll_leaves_unknown_legacy_states_untranslated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _fw151_payload(state=30)
    _, data = _poll(payload, monkeypatch)
    assert data["state"] == 30
    assert LEGACY_RAW_STATE_KEY not in data
    assert "subState" not in data


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


@pytest.mark.parametrize("state", [8, 16, 22, 24])
def test_modern_payload_keeps_legacy_only_codes_unmapped(
    state: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    modern = {**REAL_MAIN, "state": state, "subState": 8}
    markerless = {key: value for key, value in modern.items() if key != "verFWMain"}
    updater, results = _poll_sequence([modern, markerless], monkeypatch)
    for data in results:
        assert data["state"] == state
        assert data["subState"] == 8
        assert LEGACY_RAW_STATE_KEY not in data
    assert sd.get_charger_state(updater, None) == "Unknown"
    assert sd.get_charger_substate(updater, None) is None


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


# ---------------------------------------------------------------------------
# 5. A momentary zero-power sample mid-session must NOT flip the translated
#    Charging state back to Connected — that would fire a spurious
#    charging_finished/charging_started event pair and corrupt the Last
#    Session sensors. The translation tolerates a short zero-power streak
#    and reverts only when the pause persists.
# ---------------------------------------------------------------------------


def _poll_sequence(
    payloads: list[dict], monkeypatch: pytest.MonkeyPatch
) -> tuple[EveusUpdater, list[dict]]:
    responses = [_Response(p) for p in payloads]
    it = iter(responses)

    class _SeqSession:
        def post(self, url: str, **kwargs: object) -> _Response:
            return next(it)

    monkeypatch.setattr(
        common_network, "async_get_clientsession", lambda hass: _SeqSession()
    )
    # State transitions schedule bracket refreshes via async_call_later, which
    # needs a running event loop the fake hass doesn't have; the schedule
    # itself is out of scope here.
    monkeypatch.setattr(
        common_network, "async_call_later", lambda hass, delay, action: (lambda: None)
    )
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    return updater, [asyncio.run(updater._async_update_data()) for _ in payloads]


def test_legacy_energy_limit_finishes_charging_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fire = Mock()
    monkeypatch.setattr(_Hass, "bus", SimpleNamespace(async_fire=fire), raising=False)
    charging = _fw151_payload(state=3, powerMeas=1590, curMeas1=7.3, evseEnabled=0)
    limit_stop = _fw151_payload(state=16, evseEnabled=0)
    updater, _ = _poll_sequence(
        [_fw151_payload(), charging, limit_stop, limit_stop], monkeypatch,
    )

    assert sd.get_charger_state(updater, None) == "Charge Complete"
    assert sd.get_charger_substate(updater, None) == "Energy Limit"
    charging_events = [
        (event, payload) for (event, payload), _ in fire.call_args_list
        if event in (EVENT_CHARGING_STARTED, EVENT_CHARGING_FINISHED)
    ]
    assert [event for event, _ in charging_events] == [
        EVENT_CHARGING_STARTED, EVENT_CHARGING_FINISHED,
    ]
    assert charging_events[-1][1]["reason"] == "limit"
    assert sd.get_not_charging_reason(updater, None) == "Energy Limit Reached"


def test_legacy_charging_survives_single_zero_power_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    charging = _fw151_payload(state=3, powerMeas=1590, curMeas1=7.3)
    blip = _fw151_payload(state=3, powerMeas=0, curMeas1=0)
    _, results = _poll_sequence([charging, blip, charging], monkeypatch)
    assert [r["state"] for r in results] == [
        DEVICE_STATE_CHARGING,
        DEVICE_STATE_CHARGING,
        DEVICE_STATE_CHARGING,
    ]


def test_legacy_charging_reverts_after_persistent_zero_power(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    charging = _fw151_payload(state=3, powerMeas=1590, curMeas1=7.3)
    idle3 = _fw151_payload(state=3, powerMeas=0, curMeas1=0)
    _, results = _poll_sequence([charging, idle3, idle3, idle3], monkeypatch)
    # Two zero-power polls are tolerated; the third reverts to Connected.
    assert [r["state"] for r in results] == [
        DEVICE_STATE_CHARGING,
        DEVICE_STATE_CHARGING,
        DEVICE_STATE_CHARGING,
        3,
    ]


def test_legacy_state3_never_latches_without_observed_charging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    idle3 = _fw151_payload(state=3, powerMeas=0, curMeas1=0)
    _, results = _poll_sequence([idle3, idle3], monkeypatch)
    assert [r["state"] for r in results] == [3, 3]


def test_legacy_charging_latch_clears_on_other_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    charging = _fw151_payload(state=3, powerMeas=1590, curMeas1=7.3)
    idle20 = _fw151_payload(state=20, powerMeas=0, curMeas1=0)
    idle3 = _fw151_payload(state=3, powerMeas=0, curMeas1=0)
    _, results = _poll_sequence([charging, idle20, idle3], monkeypatch)
    # Once the charger reported its idle code, a later powerless 3 must not
    # resurrect Charging from the stale latch.
    assert [r["state"] for r in results] == [
        DEVICE_STATE_CHARGING,
        DEVICE_STATE_STANDBY,
        3,
    ]
