"""REVE: which ``/main`` field each GraphQL ``stationData`` field stands for.

The map is checked against two live captures from the same REVE Plus
(REVE_PLUS_1.0.0 / 2.4.0, 2026-10-15, idle): ``/main`` and a GraphQL
``stationData`` read an hour apart, and against the ``StationData`` document
the charger's own web app sends (served by the charger at ``/``, polled every
4 s). A field outside that document is one the local server may not know, and
an unknown field stops it answering, so the map may only name fields from it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from custom_components.eveus import reve_fields
from custom_components.eveus.reve import (
    REVE_UNRELIABLE_FIELDS,
    ReveExtras,
    normalize_reve_payload,
)
from custom_components.eveus.snapshot import FIELD_KEYS

FIXTURES = Path(__file__).parent / "fixtures"
STATION = json.loads((FIXTURES / "reve_graphql_station.json").read_text())["data"]["stationData"]
MAIN = json.loads((FIXTURES / "reve_main_response.json").read_text())

# Fields that move between the two captures, or that /main reports frozen.
_VOLATILE = {"systemTime", "RSSI", "vBat", "sessionTime", "sessionEnergy", "sessionMoney"}


def _app_document_paths() -> set[tuple[str, ...]]:
    """Every leaf path of the charger app's ``StationData`` document."""
    text = (FIXTURES / "reve_app_station_query.graphql").read_text()
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[{}]", text)
    stack: list[str] = []
    paths: set[tuple[str, ...]] = set()
    previous: str | None = None
    for token in tokens:
        if token == "{":
            stack.append(previous or "")
            previous = None
            continue
        if token == "}":
            if previous is not None:
                paths.add(tuple(stack[2:]) + (previous,))
            previous = None
            stack.pop()
            continue
        if previous is not None:
            paths.add(tuple(stack[2:]) + (previous,))
        previous = token
    return paths


def test_every_mapped_path_is_in_the_charger_apps_own_document() -> None:
    allowed = _app_document_paths()
    assert reve_fields.graphql_paths() <= allowed


def test_the_app_document_parser_sees_nested_leaves() -> None:
    paths = _app_document_paths()
    assert ("measurements", "temperature", "temperature2") in paths
    assert ("timeSettings", "systemTime") in paths
    assert ("measurements",) not in paths


def test_station_to_main_matches_live_main_for_every_stable_field() -> None:
    converted = reve_fields.station_to_main(STATION)
    compared = {key for key in converted if key not in _VOLATILE and key in MAIN}
    assert compared, "nothing to compare"
    assert {key: converted[key] for key in compared} == {key: MAIN[key] for key in compared}


def test_compared_fields_cover_the_live_capture() -> None:
    converted = reve_fields.station_to_main(STATION)
    for key in ("state", "subState", "currentSet", "curDesign", "evseType", "typeRelay",
                "ground", "groundCtrl", "aiStatus", "aiVoltage", "minVoltage", "IEM1",
                "IEM2_money", "totalEnergy", "voltMeas1", "temperature1", "temperature2",
                "lang", "pilot", "evseEnabled", "suspendErrors", "oneCharge",
                "sessionStarted", "timeZone", "verFWMain", "verFWWifi"):
        assert converted[key] == MAIN[key], key


def test_flags_become_the_integers_main_reports() -> None:
    converted = reve_fields.station_to_main(
        {"isChargerEnabled": True, "isOneChargeEnabled": False, "isGroundConnected": 1}
    )
    assert converted == {"evseEnabled": 1, "oneCharge": 0, "groundCtrl": 1}


def test_system_time_is_utc_iso_and_main_counts_local_seconds() -> None:
    converted = reve_fields.station_to_main(
        {"timeSettings": {"timeZone": 3, "systemTime": "2026-10-14T22:06:54Z"}}
    )
    # 2026-10-14T22:06:54Z is 1792015614; /main adds timeZone hours.
    assert converted == {"timeZone": 3, "systemTime": 1792015614 + 3 * 3600}


@pytest.mark.parametrize(
    "time_settings",
    [
        {"systemTime": "2026-10-14T22:06:54Z"},
        {"timeZone": None, "systemTime": "2026-10-14T22:06:54Z"},
        {"timeZone": 3, "systemTime": "not a time"},
        {"timeZone": 3, "systemTime": 1792015614},
        {"timeZone": 3, "systemTime": None},
    ],
)
def test_system_time_needs_a_zone_and_an_iso_string(time_settings) -> None:
    converted = reve_fields.station_to_main({"timeSettings": time_settings})
    assert "systemTime" not in converted


def test_session_limits_are_null_when_off_and_energy_is_in_watt_hours() -> None:
    off = reve_fields.station_to_main(
        {"session": {"energyLimit": None, "costLimit": None, "timeLimit": None}}
    )
    assert off == {"energyLimitS": 0, "moneyLimitS": 0, "timeLimitS": 0}
    on = reve_fields.station_to_main(
        {"session": {"energyLimit": 12500, "costLimit": 300, "timeLimit": 7200}}
    )
    assert on == {
        "energyLimit": 12.5, "energyLimitS": 1,
        "moneyLimit": 300, "moneyLimitS": 1,
        "timeLimit": 7200, "timeLimitS": 1,
    }


def test_a_field_missing_from_the_reply_is_left_out() -> None:
    assert reve_fields.station_to_main({}) == {}
    assert reve_fields.station_to_main({"measurements": None}) == {}
    assert reve_fields.station_to_main({"measurements": {"temperature": None}}) == {}


def test_a_null_value_is_left_out_not_reported_as_none() -> None:
    assert reve_fields.station_to_main({"stationState": None, "RSSI": None}) == {}


def test_station_to_main_never_mutates_its_input() -> None:
    station = json.loads(json.dumps(STATION))
    reve_fields.station_to_main(station)
    assert station == STATION


def _settings_keys() -> set[str]:
    """The legacy keys ``normalize_reve_payload`` lays over from GraphQL settings."""
    schedule = {
        "name": "", "start": 0, "end": 0, "weekdays": [0], "enabled": False,
        "value": 16, "currentLimitEnabled": False,
    }
    rate = {"name": "", "start": 0, "end": 60, "weekdays": [0], "enabled": False, "value": 1.0}
    extras = ReveExtras((schedule, schedule), 4.32, (rate, rate), 1)
    payload, _ = normalize_reve_payload({"systemTime": 1792015614, "timeZone": 3}, extras)
    return set(payload) - {"systemTime", "timeZone"}


def test_every_entity_field_has_a_graphql_source_or_is_named_as_having_none() -> None:
    sourced = reve_fields.MAIN_KEYS | _settings_keys()
    assert FIELD_KEYS <= sourced | reve_fields.NO_GRAPHQL_SOURCE
    assert not (reve_fields.NO_GRAPHQL_SOURCE & sourced)


def test_no_source_list_holds_only_entity_fields() -> None:
    assert reve_fields.NO_GRAPHQL_SOURCE <= FIELD_KEYS


def test_unreliable_main_fields_are_not_taken_from_graphql_as_is() -> None:
    # Stop is the one unreliable /main field GraphQL reports raw too; REVE's
    # real Stop is chargingCommand, laid over by normalize_reve_payload.
    assert reve_fields.MAIN_KEYS & REVE_UNRELIABLE_FIELDS == {"evseEnabled"}


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "16"])
def test_a_number_field_that_is_not_a_finite_number_is_left_out(bad) -> None:
    assert reve_fields.station_to_main({"stationCurrent": bad}) == {}


@pytest.mark.parametrize("bad", [2, 1.0, "1", None])
def test_a_flag_that_is_not_a_boolean_or_0_1_is_left_out(bad) -> None:
    assert reve_fields.station_to_main({"isOneChargeEnabled": bad}) == {}


def test_a_zero_flag_stays_zero() -> None:
    assert reve_fields.station_to_main({"isGroundConnected": 0}) == {"groundCtrl": 0}


def test_a_text_field_that_is_not_a_string_is_left_out() -> None:
    assert reve_fields.station_to_main({"wifiFWVersion": 1, "ocpp": {"stationId": 7}}) == {}


def test_a_limit_missing_from_the_session_does_not_hide_the_others() -> None:
    converted = reve_fields.station_to_main({"session": {"timeLimit": 600}})
    assert converted == {"timeLimit": 600, "timeLimitS": 1}


def test_a_limit_that_is_not_a_number_is_left_out_with_its_flag() -> None:
    assert reve_fields.station_to_main({"session": {"costLimit": "300"}}) == {}


def test_system_time_without_a_zone_suffix_is_left_out() -> None:
    converted = reve_fields.station_to_main(
        {"timeSettings": {"timeZone": 3, "systemTime": "2026-10-14T22:06:54"}}
    )
    assert converted == {"timeZone": 3}


def test_system_time_with_an_explicit_offset_is_read_as_that_instant() -> None:
    converted = reve_fields.station_to_main(
        {"timeSettings": {"timeZone": 2, "systemTime": "2026-10-15T01:06:54+03:00"}}
    )
    assert converted == {"timeZone": 2, "systemTime": 1792015614 + 2 * 3600}
