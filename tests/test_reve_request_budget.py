"""REVE: how many requests a poll costs, and what each one is for.

Measured on the live charger (2026-10-18): the measurements document alone
answered in ~80 ms, the same document with the schedules and rates added in
~90 ms. So a poll that is due for the settings asks for them in its one
measurements request, and a separate settings read — needed only while the
measurements come from ``/main`` — is one document, not three.
``chargingCommand`` is part of every measurements read already.

``/main`` stays for the fields GraphQL has no place for. Of those, the
adaptive current moves during a charge (7 → 11 → 16 → 10 A on 2026-10-16/17),
so while an adaptive charge runs ``/main`` is read once a minute. It changed
2.6 times an hour over 16 charging hours (median gap 370 s, only the start ramp
moves within seconds), so a minute loses nothing a chart can show and an
adaptive hour costs 180 requests, not 240.

REVE API reports a server that stopped answering, not a single missed reply:
one failed read turned it off for 30 s seven times on 2026-10-17.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from custom_components.eveus import reve
from test_reve_firmware import _Charger, _Response, _clock, _reve_main, _updater
from test_reve_graphql_reads import _paths


def _known_reve(monkeypatch: pytest.MonkeyPatch, charger: _Charger):
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    charger.calls.clear()
    return updater


def _queries(charger: _Charger) -> list[str]:
    return [body["query"] for path, body in charger.calls if path == "/graphql"]


# ---------------------------------------------------------------------------
# The documents
# ---------------------------------------------------------------------------


def test_the_combined_document_is_the_measurements_plus_schedules_and_rates() -> None:
    combined = _paths(reve.MEASUREMENTS_AND_SETTINGS_QUERY)
    settings = _paths(reve.SETTINGS_QUERY)
    assert combined == _paths(reve.MEASUREMENTS_QUERY) | (settings - {("chargingCommand",)})
    assert {path[0] for path in settings} == {"scheduleV2", "ratesV2", "chargingCommand"}
    for document in (reve.MEASUREMENTS_AND_SETTINGS_QUERY, reve.SETTINGS_QUERY):
        assert document.lstrip().startswith("query StationData")


def test_the_one_setting_documents_are_gone() -> None:
    assert not hasattr(reve, "RATES_QUERY")
    assert not hasattr(reve, "COMMAND_QUERY")


# ---------------------------------------------------------------------------
# Requests per poll
# ---------------------------------------------------------------------------


def test_the_first_poll_costs_main_and_one_graphql_request(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)

    data = asyncio.run(updater._async_update_data())

    assert charger.paths() == ["/main", "/graphql"]
    assert _queries(charger) == [reve.MEASUREMENTS_AND_SETTINGS_QUERY]
    assert data["sh1Start"] == 1380
    assert updater.snapshot.supports("tarif") is True
    assert updater.snapshot.supports("evseEnabled") is True


def test_a_poll_due_for_the_settings_reads_them_in_its_measurements_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    charger.schedules[0]["start"] = 1200

    asyncio.run(updater._async_update_data())
    assert _queries(charger) == [reve.MEASUREMENTS_QUERY]

    charger.calls.clear()
    now[0] += reve.EXTRAS_REFRESH_SECONDS
    data = asyncio.run(updater._async_update_data())

    assert _queries(charger) == [reve.MEASUREMENTS_AND_SETTINGS_QUERY]
    assert data["sh1Start"] == 1200


def test_settings_read_while_main_serves_the_poll_take_one_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    charger.measurements_status = 500
    now[0] += reve.EXTRAS_REFRESH_SECONDS

    asyncio.run(updater._async_update_data())

    # The combined read failed; /main served the poll. The settings are not
    # asked again from a server that has just refused.
    assert charger.paths() == ["/graphql", "/main"]
    charger.calls.clear()

    # Once the back-off is over, with the measurements still failing, the
    # settings are read alone, in one document.
    now[0] += reve.EXTRAS_RETRY_SECONDS
    asyncio.run(updater._async_update_data())
    assert _queries(charger) == [reve.MEASUREMENTS_QUERY, reve.SETTINGS_QUERY]
    assert updater.reve_diagnostics()["read_age_seconds"] == 0


def test_after_a_failed_combined_read_the_measurements_go_on_without_the_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # If the settings part were what the server refuses, asking for it with
    # every measurements read would take the measurements down with it.
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    real_post = charger.post

    def _refuse_settings(url: str, **kwargs: Any) -> _Response:
        if url.endswith("/graphql") and "scheduleV2" in json.loads(kwargs["data"])["query"]:
            charger.calls.append(("/graphql", json.loads(kwargs["data"])))
            return _Response(500, "Server has encountered an unexpected error")
        return real_post(url, **kwargs)

    charger.post = _refuse_settings
    now[0] += reve.EXTRAS_REFRESH_SECONDS
    asyncio.run(updater._async_update_data())
    now[0] += reve.MEASUREMENTS_RETRY_SECONDS
    charger.calls.clear()

    data = asyncio.run(updater._async_update_data())

    assert _queries(charger) == [reve.MEASUREMENTS_QUERY]
    assert updater.reve_diagnostics()["measurements"]["last_source"] == "graphql"
    assert data["sh1Start"] == 1380  # the last good settings read still stands


def test_a_schedule_write_with_nothing_read_yet_reads_the_settings_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    updater._reve_seen = True

    asyncio.run(updater._reve.write_schedule("sh1Enabled", 1))

    queries = _queries(charger)
    assert queries[0] == reve.SETTINGS_QUERY
    assert queries[1].lstrip().startswith("mutation")
    assert len(queries) == 2
    assert updater.reve_diagnostics()["read_age_seconds"] == 0
    assert updater.reve_schedules()[0]["enabled"] is True


@pytest.mark.parametrize(("command", "value"), [("evseEnabled", 0), ("sh2Enabled", 1)])
def test_regular_control_writes_cannot_starve_tariff_refresh(
    monkeypatch: pytest.MonkeyPatch, command: str, value: int,
) -> None:
    """Reasserting a control must not make unread app settings look freshly read."""
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    charger.rates["base"] = 8.0  # changed in the charger's app after setup
    for _ in range(reve.EXTRAS_REFRESH_SECONDS // 60):
        now[0] += 60
        assert asyncio.run(updater._command_manager.send_command(command, value)) is True
        asyncio.run(updater._async_update_data())
    assert updater.snapshot.get("tarif") == 800
    assert reve.MEASUREMENTS_AND_SETTINGS_QUERY in _queries(charger)


def test_a_schedule_write_cannot_roll_back_a_newer_stop_and_tariff_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A poll can finish while the schedule mutation is awaiting its reply."""
    _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    api = updater._reve

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        mutate = api._mutate

        async def slow_mutation(change):
            started.set()
            await release.wait()
            await mutate(change)

        monkeypatch.setattr(api, "_mutate", slow_mutation)
        writing = asyncio.create_task(api.write_schedule("sh2Enabled", 1))
        await started.wait()
        charger.charging_command = 2  # stopped in the app meanwhile
        charger.rates["base"] = 8.0
        await api.refresh()
        assert api.extras.charging_command == 2
        release.set()
        await writing
        charger.measurements_status = 500
        await updater._async_update_data()  # fallback must keep the newer settings
        assert updater.snapshot.get("evseEnabled") == 1
        assert updater.snapshot.get("tarif") == 800

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# /main for the adaptive current
# ---------------------------------------------------------------------------


def _charging(charger: _Charger, *, adaptive: int = 1) -> None:
    charger.station["stationState"] = 4
    charger.station["adaptiveModeStatus"] = adaptive
    charger.main["state"] = 4
    charger.main["aiStatus"] = adaptive


def test_an_adaptive_charge_reads_main_every_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    _charging(charger)

    seen = []
    for poll in range(4):
        now[0] += 30
        charger.main["aiModecurrent"] = 11 + poll
        data = asyncio.run(updater._async_update_data())
        seen.append((charger.paths()[-1], data["aiModecurrent"]))
        charger.calls.clear()

    # Polls at +30 and +90 s leave /main alone and show the figure of the
    # read before (the first one's is the 7 A of the setup read); +60 and
    # +120 s read it.
    assert seen == [("/graphql", 7), ("/main", 12), ("/graphql", 12), ("/main", 14)]


def test_an_adaptive_hour_costs_120_graphql_and_60_main_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    _charging(charger)

    for _ in range(120):
        now[0] += 30
        asyncio.run(updater._async_update_data())

    assert charger.paths().count("/graphql") == 120
    assert charger.paths().count("/main") == 60


def test_main_is_read_after_fifty_seconds_not_before(monkeypatch: pytest.MonkeyPatch) -> None:
    # The poll cadence wanders by a second or two; 50 s keeps every second poll
    # on the minute without a late one skipping a whole extra cycle.
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    _charging(charger)

    now[0] += reve.ADAPTIVE_MAIN_REFRESH_SECONDS - 1
    asyncio.run(updater._async_update_data())
    assert charger.paths() == ["/graphql"]
    now[0] += 1
    asyncio.run(updater._async_update_data())
    assert charger.paths() == ["/graphql", "/graphql", "/main"]


@pytest.mark.parametrize(("state", "adaptive"), [(4, 0), (2, 1), (5, 1)])
def test_main_waits_five_minutes_unless_an_adaptive_charge_runs(
    monkeypatch: pytest.MonkeyPatch, state: int, adaptive: int,
) -> None:
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    charger.station["stationState"] = state
    charger.station["adaptiveModeStatus"] = adaptive

    asyncio.run(updater._async_update_data())
    assert charger.paths() == ["/graphql"]

    now[0] += reve.MAIN_RESIDUE_REFRESH_SECONDS - 1
    asyncio.run(updater._async_update_data())
    assert charger.paths() == ["/graphql", "/graphql"]
    now[0] += 1
    asyncio.run(updater._async_update_data())
    assert charger.paths()[-1] == "/main"


def test_a_paused_adaptive_session_still_reads_main(monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    charger.station["stationState"] = 6
    charger.station["adaptiveModeStatus"] = 2

    now[0] += 60
    asyncio.run(updater._async_update_data())

    assert charger.paths() == ["/graphql", "/main"]


def test_a_main_that_fails_during_an_adaptive_charge_is_asked_every_five_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A /main that times out would otherwise add its whole timeout to every poll.
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    _charging(charger)
    charger.main_status = 500

    now[0] += 60
    asyncio.run(updater._async_update_data())
    now[0] += 60
    asyncio.run(updater._async_update_data())
    assert charger.paths() == ["/graphql", "/main", "/graphql"]

    charger.main_status = 200
    now[0] += reve.MAIN_RESIDUE_REFRESH_SECONDS
    asyncio.run(updater._async_update_data())
    now[0] += 60
    asyncio.run(updater._async_update_data())
    assert charger.paths()[3:] == ["/graphql", "/main", "/graphql", "/main"]


# ---------------------------------------------------------------------------
# REVE API: a server that stopped answering, not one missed reply
# ---------------------------------------------------------------------------


def test_one_failed_read_leaves_reve_api_on(monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    charger.measurements_status = 500

    asyncio.run(updater._async_update_data())
    assert updater.reve_api_answering is True

    charger.measurements_status = 200
    now[0] += reve.MEASUREMENTS_RETRY_SECONDS
    asyncio.run(updater._async_update_data())
    assert updater.reve_api_answering is True


def test_two_failed_reads_in_a_row_turn_reve_api_off_until_one_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    charger.measurements_status = 500

    asyncio.run(updater._async_update_data())
    now[0] += reve.MEASUREMENTS_RETRY_SECONDS
    asyncio.run(updater._async_update_data())
    assert updater.reve_api_answering is False

    charger.measurements_status = 200
    now[0] += reve.MEASUREMENTS_RETRY_SECONDS
    asyncio.run(updater._async_update_data())
    assert updater.reve_api_answering is True


def test_settings_reads_that_keep_failing_turn_reve_api_off_and_keep_it_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The measurements answer, the settings do not: REVE API must not flip
    # on and off around every settings retry (04b7949).
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _known_reve(monkeypatch, charger)
    real_post = charger.post

    def _refuse_settings(url: str, **kwargs: Any) -> _Response:
        if url.endswith("/graphql") and "scheduleV2" in json.loads(kwargs["data"])["query"]:
            return _Response(500, "Server has encountered an unexpected error")
        return real_post(url, **kwargs)

    charger.post = _refuse_settings
    now[0] += reve.EXTRAS_REFRESH_SECONDS
    asyncio.run(updater._async_update_data())  # the combined read fails
    assert updater.reve_api_answering is True
    now[0] += reve.MEASUREMENTS_RETRY_SECONDS
    asyncio.run(updater._async_update_data())  # measurements answer
    assert updater.reve_api_answering is True

    now[0] += reve.EXTRAS_RETRY_SECONDS
    asyncio.run(updater._async_update_data())  # the settings read alone fails too
    assert updater.reve_api_answering is False
    assert asyncio.run(updater._command_manager.send_command("evseEnabled", 0)) is True
    assert updater.reve_api_answering is False, "a write did not repair settings reads"
    for _ in range(3):
        now[0] += reve.MEASUREMENTS_RETRY_SECONDS
        asyncio.run(updater._async_update_data())
        assert updater.reve_api_answering is False

    charger.post = real_post
    now[0] += reve.EXTRAS_RETRY_SECONDS
    asyncio.run(updater._async_update_data())
    assert updater.reve_api_answering is True


def test_a_fresh_reve_api_reads_main_at_once() -> None:
    from types import SimpleNamespace

    assert reve.ReveApi(SimpleNamespace()).main_residue_due() is True
