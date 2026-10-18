"""REVE: measurements read from GraphQL, ``/main`` only as the fallback.

The vendor calls ``/main`` and ``/pageEvent`` leftovers of the move to GraphQL,
and on REVE ``/main`` already misreports part of what it carries (frozen
session, garbage tariffs). Once a charger has identified as REVE, each poll
reads the measurements from the local GraphQL ``stationData`` — the very
document the charger's own web app polls every 4 s, cut down to the fields the
entities read — and turns them into the ``/main`` keys the entities already
use. ``/main`` is read only when GraphQL does not answer, and every five
minutes for the few fields GraphQL has no place for (maker, model, serial
number, the limits' master switch, the adaptive current).
"""
from __future__ import annotations

import asyncio
import copy
import re
from pathlib import Path
from typing import Any

import pytest

from custom_components.eveus import reve, reve_fields
from custom_components.eveus.common_network import EveusUnreachable
from test_reve_firmware import _Charger, _clock, _load, _reve_main, _updater

_FIXTURES = Path(__file__).parent / "fixtures"


def _paths(document: str) -> set[tuple[str, ...]]:
    """Every leaf path of a ``query StationData { stationData { … } }`` document."""
    # From the query's own brace: "query StationData" names the operation.
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[{}]", document[document.index("{"):])
    stack: list[str] = []
    paths: set[tuple[str, ...]] = set()
    previous: str | None = None
    for token in tokens:
        if token == "{":
            stack.append(previous or "")
            previous = None
        elif token == "}":
            if previous is not None:
                paths.add(tuple(stack[2:]) + (previous,))
            previous = None
            stack.pop()
        else:
            if previous is not None:
                paths.add(tuple(stack[2:]) + (previous,))
            previous = token
    return paths


def _station() -> dict[str, Any]:
    return copy.deepcopy(_load("reve_graphql_station.json")["data"]["stationData"])


def _known_reve(monkeypatch: pytest.MonkeyPatch, charger: _Charger):
    """An updater whose first poll (``/main``) has identified the charger as REVE."""
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    charger.calls.clear()
    return updater


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


def test_the_measurements_document_asks_for_exactly_the_mapped_fields() -> None:
    asked = _paths(reve.MEASUREMENTS_QUERY)
    assert asked == reve_fields.graphql_paths() | {("chargingCommand",)}
    app = _paths((_FIXTURES / "reve_app_station_query.graphql").read_text())
    assert asked <= app
    assert reve.MEASUREMENTS_QUERY.lstrip().startswith("query StationData")


def test_no_secret_is_ever_asked_for() -> None:
    for secret in ("ocppAuthKey", "chargerSsidPassword", "stationKey", "modemPIN"):
        assert secret not in reve.MEASUREMENTS_QUERY


# ---------------------------------------------------------------------------
# Polls once the charger is known to be REVE
# ---------------------------------------------------------------------------


def test_a_known_reve_charger_is_polled_through_graphql_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    charger.station["measurements"]["voltMeasurement1"] = 231

    data = asyncio.run(updater._async_update_data())

    assert charger.paths() == ["/graphql"]
    assert charger.calls[0][1]["query"] == reve.MEASUREMENTS_QUERY
    assert charger.calls[0][1]["operationName"] == "StationData"
    assert data["voltMeas1"] == 231
    # The settings read on the first poll still stand.
    assert data["sh1Start"] == 1380
    assert updater.last_update_success is True
    assert updater.firmware_family == "reve"


def test_the_first_poll_reads_main_then_the_measurements_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    # /main recognises the firmware; the same poll then shows GraphQL's
    # measurements, so a restart never publishes /main's frozen session.
    charger = _Charger(_reve_main())
    charger.station["session"]["sessionTime"] = 120
    updater = _updater(monkeypatch, charger)

    data = asyncio.run(updater._async_update_data())

    assert charger.paths()[:2] == ["/main", "/graphql"]
    assert charger.calls[1][1]["query"] == reve.MEASUREMENTS_QUERY
    assert data["sessionTime"] == 120
    assert data["manufacturer"] == "Reve"
    assert updater.reve_diagnostics()["measurements"]["last_source"] == "graphql"


def test_a_first_poll_whose_measurements_fail_shows_main(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    charger.measurements_status = 500
    updater = _updater(monkeypatch, charger)

    data = asyncio.run(updater._async_update_data())

    assert updater.last_update_success is True
    assert data["state"] == 2
    assert updater.reve_diagnostics()["measurements"]["main_fallbacks"] == 0


def test_a_first_poll_whose_measurements_fail_validation_shows_main(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    charger.station["stationCurrent"] = 99  # above this 16 A charger's maximum
    updater = _updater(monkeypatch, charger)

    data = asyncio.run(updater._async_update_data())

    assert updater.last_update_success is True
    assert data["currentSet"] == 7


def test_a_graphql_outage_falls_back_to_main_in_the_same_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    charger.measurements_status = 500
    charger.main["voltMeas1"] = 229

    data = asyncio.run(updater._async_update_data())

    assert charger.paths() == ["/graphql", "/main"]
    assert data["voltMeas1"] == 229
    assert updater.last_update_success is True
    assert updater.reve_diagnostics()["measurements"]["main_fallbacks"] == 1
    assert updater.reve_api_answering is False


def test_a_reply_without_the_state_is_a_failed_read(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    del charger.station["stationState"]

    asyncio.run(updater._async_update_data())

    assert charger.paths() == ["/graphql", "/main"]


def test_after_a_failed_read_graphql_waits_before_it_is_tried_again(monkeypatch: pytest.MonkeyPatch) -> None:
    # A GraphQL server that times out costs its whole timeout; trying it on
    # every poll would delay every poll by that much.
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    charger.measurements_status = 500
    asyncio.run(updater._async_update_data())
    charger.measurements_status = 200
    charger.calls.clear()

    now[0] += reve.MEASUREMENTS_RETRY_SECONDS - 1
    asyncio.run(updater._async_update_data())
    assert charger.paths() == ["/main"]

    charger.calls.clear()
    now[0] += 1
    asyncio.run(updater._async_update_data())
    assert charger.paths() == ["/graphql"]


def test_measurements_that_fail_validation_fall_back_to_main(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    charger.station["stationCurrent"] = 99  # above this 16 A charger's maximum

    data = asyncio.run(updater._async_update_data())

    assert charger.paths() == ["/graphql", "/main"]
    assert updater.last_update_success is True
    assert data["currentSet"] == 7
    assert updater.reve_diagnostics()["measurements"]["graphql_failures"] == 1


def test_latency_leaves_out_the_main_read_for_its_own_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    real_post = charger.post

    def slow_main(url: str, **kwargs: Any) -> Any:
        if url.endswith("/main"):
            now[0] += 5.0
        return real_post(url, **kwargs)

    charger.post = slow_main
    now[0] += reve.MAIN_RESIDUE_REFRESH_SECONDS
    asyncio.run(updater._async_update_data())

    assert charger.paths()[:2] == ["/graphql", "/main"]
    assert updater.connection_quality["latency_avg"] < 1.0


def test_both_sources_silent_is_an_ordinary_failed_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    charger.measurements_status = 500
    charger.main_status = 503

    with pytest.raises(EveusUnreachable):
        asyncio.run(updater._async_update_data())


def test_latency_is_the_answering_sources_own(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    assert updater.connection_quality["latency_avg"] < 1.0


# ---------------------------------------------------------------------------
# What only /main carries
# ---------------------------------------------------------------------------


def test_fields_only_main_carries_are_kept_from_the_last_main(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)

    data = asyncio.run(updater._async_update_data())

    assert charger.paths() == ["/graphql"]
    assert data["manufacturer"] == "Reve"
    assert data["model"] == "Plus"
    assert data["serialNum"] == "EVS-1M00000K3897"
    assert data["suspendLimits"] == 1
    assert data["aiModecurrent"] == 7
    assert updater.snapshot.supports("suspendLimits") is True


def test_a_value_graphql_reports_wins_over_the_kept_main_one(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    charger.station["session"]["energyLimit"] = 3000

    data = asyncio.run(updater._async_update_data())

    assert data["energyLimit"] == 3.0
    assert data["energyLimitS"] == 1


def test_the_main_only_fields_are_refreshed_every_five_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    charger.main["suspendLimits"] = 0

    now[0] += reve.MAIN_RESIDUE_REFRESH_SECONDS - 1
    data = asyncio.run(updater._async_update_data())
    assert charger.paths() == ["/graphql"]
    assert data["suspendLimits"] == 1

    charger.calls.clear()
    now[0] += 1
    data = asyncio.run(updater._async_update_data())
    # Measurements, then /main for its own fields (the five-minute settings
    # read follows in the same poll).
    assert charger.paths()[:2] == ["/graphql", "/main"]
    assert data["suspendLimits"] == 0


def test_a_failed_refresh_of_them_never_fails_the_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    charger.main_status = 503

    now[0] += reve.MAIN_RESIDUE_REFRESH_SECONDS
    data = asyncio.run(updater._async_update_data())

    assert updater.last_update_success is True
    assert data["suspendLimits"] == 1


def test_main_only_fields_kept_too_long_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    charger.main_status = 503

    now[0] += reve.MAIN_RESIDUE_MAX_AGE_SECONDS + 1
    data = asyncio.run(updater._async_update_data())

    assert "suspendLimits" not in data
    assert updater.snapshot.supports("suspendLimits") is False
    # The device-registry strings are not entity values: they stay.
    assert data["manufacturer"] == "Reve"


# ---------------------------------------------------------------------------
# The session /main reports frozen
# ---------------------------------------------------------------------------


def test_the_frozen_main_session_never_reaches_the_entities(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())  # /main: sessionTime 52745, frozen since the old firmware
    charger.measurements_status = 500
    updater = _updater(monkeypatch, charger)

    data = asyncio.run(updater._async_update_data())

    assert "sessionTime" not in data
    assert updater.snapshot.supports("sessionTime") is False


def test_a_fallback_poll_holds_the_last_graphql_session(monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    charger.station["session"].update(sessionTime=600, sessionEnergy=0.58, sessionCost=2.51)
    asyncio.run(updater._async_update_data())

    charger.measurements_status = 500
    now[0] += reve.SESSION_HOLD_SECONDS
    charger.calls.clear()
    data = asyncio.run(updater._async_update_data())
    assert charger.paths()[:2] == ["/graphql", "/main"]
    assert (data["sessionTime"], data["sessionEnergy"], data["sessionMoney"]) == (600, 0.58, 2.51)

    now[0] += reve.MEASUREMENTS_RETRY_SECONDS + 1
    data = asyncio.run(updater._async_update_data())
    assert "sessionTime" not in data


# ---------------------------------------------------------------------------
# Stop read with the measurements
# ---------------------------------------------------------------------------


def test_stop_set_in_the_app_shows_on_the_next_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    charger.charging_command = 2  # stopped from the vendor app

    data = asyncio.run(updater._async_update_data())

    assert charger.paths() == ["/graphql"]
    assert data["evseEnabled"] == 1


@pytest.mark.parametrize("unusable", [7, -1, "2", None, True])
def test_an_unusable_charging_command_keeps_the_last_read(monkeypatch: pytest.MonkeyPatch, unusable: Any) -> None:
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    charger.charging_command = 2
    assert asyncio.run(updater._async_update_data())["evseEnabled"] == 1
    charger.charging_command = unusable

    data = asyncio.run(updater._async_update_data())

    assert data["evseEnabled"] == 1, "Stop stays as last read"


def test_diagnostics_count_where_the_reads_came_from(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    charger.measurements_status = 500
    asyncio.run(updater._async_update_data())

    measurements = updater.reve_diagnostics()["measurements"]
    # The first poll's own read, then the second poll's.
    assert measurements["graphql_reads"] == 2
    assert measurements["graphql_failures"] == 1
    assert measurements["main_fallbacks"] == 1
    assert measurements["last_source"] == "main"


def test_measurements_above_this_models_current_fall_back_to_main(monkeypatch: pytest.MonkeyPatch) -> None:
    # 20 A passes the largest model's bound, not this 16 A charger's: the
    # GraphQL read is checked against the configured model, so the poll falls
    # back to /main instead of failing as a whole.
    from custom_components.eveus.const import MODEL_16A

    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger, MODEL_16A)
    asyncio.run(updater._async_update_data())
    charger.calls.clear()
    charger.station["stationCurrent"] = 20
    charger.main["voltMeas1"] = 229

    data = asyncio.run(updater._async_update_data())

    assert updater.last_update_success is True
    assert charger.paths() == ["/graphql", "/main"]
    assert data["voltMeas1"] == 229


def test_a_known_reve_charger_stays_reve_on_a_main_without_its_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    charger.measurements_status = 500
    for marker in ("manufacturer", "verFWWifi"):
        charger.main.pop(marker, None)

    data = asyncio.run(updater._async_update_data())

    assert "leakValue" not in data, "REVE's raw leakage counts are still dropped"
    assert updater.firmware_family == "reve"
