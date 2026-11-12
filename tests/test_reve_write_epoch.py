"""REVE reads and writes that overlap, at the ``ReveApi`` level.

Polls and commands hold different locks, so a settings read can start before a
write and land after it. Such a read may not replace what the write set (the
charging command, the schedules); a read with no write around it is taken
whole. Also: the kept limit values, what a fresh API reports, and a station
that answers ``listSessions`` with nothing usable.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from custom_components.eveus import reve
from custom_components.eveus.reve import ReveApi, ReveExtras


def _extras(command: int | None, schedule: str, rate: float | None = None) -> ReveExtras:
    return ReveExtras(
        schedules=({"name": schedule},), base_rate=1.0, rates=(),
        charging_command=command, active_rate=rate,
    )


def _api(updater: Any | None = None) -> tuple[ReveApi, list[dict[str, Any]]]:
    """An API on an updater double without ``_shutting_down`` (it is read with
    a default), whose writes are accepted after yielding once."""
    api = ReveApi(updater if updater is not None else SimpleNamespace())
    sent: list[dict[str, Any]] = []

    async def post(operation: str, _document: str, variables: Any = None) -> dict[str, Any]:
        await asyncio.sleep(0)
        if operation == "UpdateStationData":
            sent.append(variables["input"])
            return {"updateStationData": True}
        return {}

    api._post = post  # type: ignore[method-assign]
    return api, sent


def test_a_write_goes_out_on_an_updater_that_does_not_say_it_is_shutting_down() -> None:
    api, sent = _api()
    asyncio.run(api._mutate({"x": 1}))
    assert sent == [{"x": 1}]


def test_a_read_that_a_whole_write_landed_inside_keeps_what_the_write_set() -> None:
    api, _sent = _api()
    api._extras = _extras(1, "written")
    # An earlier write: the epoch is no longer where it started.
    asyncio.run(api._mutate({"first": 1}))

    async def read_across_a_write() -> ReveExtras:
        await api._mutate({"second": 1})
        return _extras(0, "read", rate=5.0)

    api._read_extras = read_across_a_write  # type: ignore[method-assign]
    asyncio.run(api.refresh())
    assert api._extras.charging_command == 1
    assert api._extras.schedules == ({"name": "written"},)
    assert api._extras.active_rate == 5.0  # the rest of the read stands


def test_after_overlapping_writes_a_quiet_read_is_taken_whole() -> None:
    api, sent = _api()
    api._extras = _extras(1, "written")

    async def two_at_once() -> None:
        await asyncio.gather(api._mutate({"a": 1}), api._mutate({"b": 2}))

    asyncio.run(two_at_once())
    assert len(sent) == 2
    assert api._read_mark() is not None  # no write is in flight any more

    async def quiet_read() -> ReveExtras:
        return _extras(0, "read")

    api._read_extras = quiet_read  # type: ignore[method-assign]
    asyncio.run(api.refresh())
    assert api._extras.charging_command == 0
    assert api._extras.schedules == ({"name": "read"},)


def test_a_kept_limit_value_tells_the_listener_only_when_it_changes() -> None:
    api, _sent = _api()
    calls: list[None] = []
    api.on_limit_values_changed = lambda: calls.append(None)
    api._keep_limit_value("energyLimit", 20)
    api._keep_limit_value("energyLimit", 20)
    assert calls == [None]
    api._keep_limit_value("energyLimit", 30)
    assert len(calls) == 2


def test_a_fresh_api_keeps_a_limit_value_with_no_listener() -> None:
    api, _sent = _api()
    api._keep_limit_value("energyLimit", 20)
    assert api.limit_values() == {"energyLimit": 20}


def test_a_saved_limit_value_must_be_above_zero() -> None:
    api, _sent = _api()
    api.seed_limit_values({"energyLimit": 0, "timeLimit": 1})
    assert api.limit_values() == {"timeLimit": 1}


def test_a_fresh_api_reports_a_measured_session_and_no_station_figures() -> None:
    api, _sent = _api()
    assert api.session_measured is True
    assert api.station_system == {}
    assert api.diagnostics()["station"] == {}


def test_a_main_poll_is_not_a_measured_session_and_each_fallback_counts() -> None:
    api, _sent = _api()
    api.from_main({"state": 2}, fallback=True)
    assert api.session_measured is False
    api.from_main({"state": 2}, fallback=True)
    api.from_main({"state": 2}, fallback=False)
    assert api.diagnostics()["measurements"]["main_fallbacks"] == 2


def test_a_session_listing_that_is_not_a_mapping_reads_as_no_session() -> None:
    api, _sent = _api()

    async def post(*_args: Any) -> dict[str, Any]:
        return {"listSessions": None}

    api._post = post  # type: ignore[method-assign]
    assert asyncio.run(api.read_last_session()) == reve.NO_SESSION
