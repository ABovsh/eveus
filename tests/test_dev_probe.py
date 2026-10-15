"""Developer probe of the charger's two APIs (branch `reve` only, never released).

Removed together with ``custom_components/eveus/dev_probe.py``; see its docstring.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest

from custom_components.eveus import dev_probe
from custom_components.eveus._payload import PayloadError
from custom_components.eveus.dev_probe import (
    API_GRAPHQL,
    API_MAIN,
    GRAPHQL_PROBE_SECONDS,
    Outages,
    error_kind,
)
from test_reve_firmware import _Charger, _classic_main, _clock, _reve_main, _updater

_DAY = datetime(2026, 10, 15, 9, 0, 0)


def _at(seconds: float) -> datetime:
    return _DAY + timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# Outages: runs of failed requests ended by a success
# ---------------------------------------------------------------------------


def test_a_run_of_failures_ended_by_a_success_is_one_outage() -> None:
    outages = Outages(_DAY.date())
    assert outages.record(_at(0), True, None) is False
    assert outages.record(_at(60), False, "TimeoutError") is True
    assert outages.down_since == _at(60)
    # Further failures of the same outage change nothing visible.
    assert outages.record(_at(120), False, "ClientResponseError 500") is False
    assert outages.record(_at(200), True, None) is True

    assert outages.count == 1
    assert outages.down_since is None
    assert outages.down_seconds == 140
    assert outages.last_outage == "09:01:00–09:03:20 (140 s, TimeoutError)"
    assert outages.attributes() == {
        "down_since": None,
        "last_outage": "09:01:00–09:03:20 (140 s, TimeoutError)",
        "down_seconds_today": 140,
    }


def test_each_outage_counts_once() -> None:
    outages = Outages(_DAY.date())
    for start in (0, 100, 200):
        outages.record(_at(start), False, "TimeoutError")
        outages.record(_at(start + 15), True, None)
    assert outages.count == 3
    assert outages.down_seconds == 45


def test_a_new_day_starts_from_zero_but_keeps_an_outage_in_progress() -> None:
    outages = Outages(_DAY.date())
    outages.record(_at(0), False, "TimeoutError")
    outages.record(_at(10), True, None)
    outages.record(_at(20), False, "TimeoutError")

    assert outages.roll(_DAY.date() + timedelta(days=1)) is True
    assert outages.count == 1
    assert outages.down_seconds == 0
    assert outages.roll(_DAY.date() + timedelta(days=1)) is False


def test_an_outage_across_midnight_counts_only_todays_part() -> None:
    late = datetime(2026, 10, 15, 23, 59, 0)
    outages = Outages(late.date())
    outages.record(late, False, "TimeoutError")
    outages.record(late + timedelta(minutes=2), True, None)
    assert outages.count == 1
    assert outages.down_seconds == 60


def test_interrupt_closes_an_open_outage_at_its_last_failure() -> None:
    # A Home Assistant restart: nothing is known about the time it was down.
    outages = Outages(_DAY.date())
    outages.record(_at(0), False, "TimeoutError")
    outages.record(_at(30), False, "TimeoutError")
    assert outages.interrupt() is True
    assert outages.down_since is None
    assert outages.down_seconds == 30
    assert outages.interrupt() is False


def test_error_kinds_name_the_http_status_and_the_payload_rule() -> None:
    assert error_kind(asyncio.TimeoutError()) == "TimeoutError"
    assert (
        error_kind(aiohttp.ClientResponseError(request_info=None, history=(), status=500))
        == "ClientResponseError 500"
    )
    assert error_kind(PayloadError("missing_state", "no state")) == "PayloadError missing_state"


# ---------------------------------------------------------------------------
# The probe on a coordinator
# ---------------------------------------------------------------------------


class _Hass:
    def __init__(self, root: Path) -> None:
        self.config = SimpleNamespace(path=lambda *parts: str(root.joinpath(*parts)))
        self.tasks: list[asyncio.Task[Any]] = []

    async def async_add_executor_job(self, func: Any, *args: Any) -> Any:
        return func(*args)

    def async_create_background_task(self, coro: Any, _name: str) -> asyncio.Task[Any]:
        task = asyncio.ensure_future(coro)
        self.tasks.append(task)
        return task


class _Entry:
    def __init__(self) -> None:
        self.unloads: list[Any] = []

    def async_on_unload(self, func: Any) -> None:
        self.unloads.append(func)

    async def unload(self) -> None:
        for func in self.unloads:
            result = func()
            if asyncio.iscoroutine(result):
                await result


@pytest.fixture(autouse=True)
def _no_midnight_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dev_probe, "async_track_time_change", lambda *_a, **_k: lambda: None)


@pytest.fixture(autouse=True)
def _read_timer(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """The probe's own GraphQL timer, fired by hand: [callback, interval]."""
    timer: list[Any] = []

    def track(_hass: Any, action: Any, interval: timedelta) -> Any:
        timer[:] = [action, interval]
        return lambda: timer.clear()

    monkeypatch.setattr(dev_probe, "async_track_time_interval", track, raising=False)
    return timer


def _lines(root: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted((root / dev_probe.PROBE_DIR).glob("*.jsonl")):
        out.extend(json.loads(line) for line in path.read_text().splitlines())
    return out


async def _settle(hass: _Hass) -> None:
    while any(not task.done() for task in hass.tasks):
        await asyncio.gather(*hass.tasks)


async def _poll(updater: Any) -> None:
    try:
        await updater._async_update_data()
    except Exception:  # noqa: BLE001 - a failed poll is part of the scenario
        pass


def test_without_the_folder_the_probe_does_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    hass, entry = _Hass(tmp_path), _Entry()
    record_success = updater._record_success

    probe = asyncio.run(dev_probe.async_attach(hass, entry, updater))

    assert probe is None
    assert entry.unloads == []
    assert updater._record_success == record_success
    assert dev_probe.create_sensors(updater, 1) == []
    assert not (tmp_path / dev_probe.PROBE_DIR).exists()


def test_every_exchange_is_logged_with_its_outcome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / dev_probe.PROBE_DIR).mkdir()
    _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    hass, entry = _Hass(tmp_path), _Entry()

    async def scenario() -> None:
        await dev_probe.async_attach(hass, entry, updater)
        await _poll(updater)
        await _settle(hass)
        await entry.unload()

    asyncio.run(scenario())
    lines = _lines(tmp_path)

    assert [line.get("event") or line["api"] for line in lines] == [
        # The GraphQL read completes inside the poll, before /main's outcome
        # is recorded; the firmware is known from that /main reply.
        "start", API_GRAPHQL, API_GRAPHQL, API_GRAPHQL, "firmware", API_MAIN,
    ]
    assert lines[0]["graphql_probe_s"] == GRAPHQL_PROBE_SECONDS
    assert lines[4]["wifi"] == "REVE_PLUS_1.0.0"
    # Each read document is named: they all share the operation StationData.
    assert [line["op"] for line in lines[1:4]] == ["ScheduleV2", "RatesV2", "ChargingCommand"]
    graphql, main = lines[1], lines[5]
    assert main["ok"] is True and main["op"] == "poll"
    assert main["st"] == _reve_main()["state"]
    assert isinstance(main["ms"], int)
    assert graphql == {**graphql, "op": "ScheduleV2", "ok": True}
    assert all(isinstance(datetime.fromisoformat(line["ts"]), datetime) for line in lines)


def test_graphql_is_read_every_minute_and_while_main_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / dev_probe.PROBE_DIR).mkdir()
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    hass, entry = _Hass(tmp_path), _Entry()
    real_post = charger.post

    async def scenario() -> None:
        await dev_probe.async_attach(hass, entry, updater)
        await _poll(updater)
        now[0] += GRAPHQL_PROBE_SECONDS
        await _poll(updater)  # healthy: read again a minute later, not five
        now[0] += GRAPHQL_PROBE_SECONDS

        def main_down(url: str, **kwargs: Any) -> Any:
            if url.endswith("/main"):
                raise asyncio.TimeoutError()
            return real_post(url, **kwargs)

        charger.post = main_down
        await _poll(updater)  # /main fails: GraphQL is still asked
        await _settle(hass)
        await entry.unload()

    asyncio.run(scenario())
    exchanges = [(line["api"], line["ok"]) for line in _lines(tmp_path) if "api" in line]

    assert exchanges == [
        *[(API_GRAPHQL, True)] * 3, (API_MAIN, True),
        *[(API_GRAPHQL, True)] * 3, (API_MAIN, True),
        (API_MAIN, False), *[(API_GRAPHQL, True)] * 3,
    ]
    main_failure = [line for line in _lines(tmp_path) if line.get("api") == API_MAIN][-1]
    assert main_failure["err"] == "TimeoutError"
    assert updater.dev_probe.outages[API_MAIN].down_since is not None


def test_graphql_is_read_every_thirty_seconds_between_main_polls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _read_timer: list[Any]
) -> None:
    # Asked by the vendor (2026-10-15): does a 30 s request interval change
    # how often the Wi-Fi module drops? /main keeps its own cadence, so the
    # probe reads GraphQL on a timer of its own.
    (tmp_path / dev_probe.PROBE_DIR).mkdir()
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    hass, entry = _Hass(tmp_path), _Entry()

    async def scenario() -> None:
        await dev_probe.async_attach(hass, entry, updater)
        await _poll(updater)
        tick, interval = _read_timer
        assert interval == timedelta(seconds=30)
        tick(None)  # not due yet: the poll has just read
        await _settle(hass)
        now[0] += GRAPHQL_PROBE_SECONDS
        tick(None)  # due, with no /main poll in between
        await _settle(hass)
        await entry.unload()

    asyncio.run(scenario())
    exchanges = [(line["api"], line["ok"]) for line in _lines(tmp_path) if "api" in line]

    assert GRAPHQL_PROBE_SECONDS == 30
    assert exchanges == [
        *[(API_GRAPHQL, True)] * 3, (API_MAIN, True),
        *[(API_GRAPHQL, True)] * 3,
    ]
    assert _read_timer == []  # the timer stops with the entry


def test_a_file_in_the_probe_folder_sets_the_read_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _read_timer: list[Any]
) -> None:
    # The Wi-Fi test switches between 30 s and 5 min without a deploy: the
    # interval is read when the probe starts (an entry reload restarts it).
    folder = tmp_path / dev_probe.PROBE_DIR
    folder.mkdir()
    (folder / dev_probe.INTERVAL_FILE).write_text("300\n")
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    hass, entry = _Hass(tmp_path), _Entry()

    async def scenario() -> None:
        await dev_probe.async_attach(hass, entry, updater)
        await _poll(updater)
        tick, interval = _read_timer
        assert interval == timedelta(seconds=300)
        now[0] += 60
        await _poll(updater)  # a minute later: not due at 5 min
        now[0] += 240
        tick(None)  # five minutes after the first read: due
        await _settle(hass)
        await entry.unload()

    asyncio.run(scenario())
    lines = _lines(tmp_path)
    assert lines[0]["graphql_probe_s"] == 300
    exchanges = [line["api"] for line in lines if "api" in line]
    assert exchanges == [API_GRAPHQL] * 3 + [API_MAIN] * 2 + [API_GRAPHQL] * 3


def test_an_unreadable_interval_file_keeps_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _read_timer: list[Any]
) -> None:
    folder = tmp_path / dev_probe.PROBE_DIR
    folder.mkdir()
    (folder / dev_probe.INTERVAL_FILE).write_text("often")
    _clock(monkeypatch)
    updater = _updater(monkeypatch, _Charger(_reve_main()))
    hass, entry = _Hass(tmp_path), _Entry()

    async def scenario() -> None:
        await dev_probe.async_attach(hass, entry, updater)
        await entry.unload()

    asyncio.run(scenario())
    assert _lines(tmp_path)[0]["graphql_probe_s"] == GRAPHQL_PROBE_SECONDS


def test_graphql_failures_count_as_outages_and_reach_the_sensor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / dev_probe.PROBE_DIR).mkdir()
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    hass, entry = _Hass(tmp_path), _Entry()
    writes: list[int] = []

    async def scenario() -> list[Any]:
        await dev_probe.async_attach(hass, entry, updater)
        sensors = dev_probe.create_sensors(updater, 1)
        updater.dev_probe.add_listener(lambda: writes.append(sensors[1].native_value))
        await _poll(updater)
        charger.graphql_status = 500
        for _ in range(3):
            now[0] += GRAPHQL_PROBE_SECONDS
            await _poll(updater)
        charger.graphql_status = 200
        now[0] += GRAPHQL_PROBE_SECONDS
        await _poll(updater)
        await _settle(hass)
        await entry.unload()
        return sensors

    sensors = asyncio.run(scenario())
    graphql_lines = [line for line in _lines(tmp_path) if line.get("api") == API_GRAPHQL]
    # A refused read stops at its first document: one request a minute.
    assert [line["ok"] for line in graphql_lines] == [True] * 3 + [False] * 3 + [True] * 3
    assert graphql_lines[3]["err"] == "ClientResponseError 500"
    assert graphql_lines[3]["op"] == "ScheduleV2"

    main_sensor, graphql_sensor = sensors
    assert main_sensor.unique_id == "eveus_dev_main_outages_today"
    assert graphql_sensor.unique_id == "eveus_dev_graphql_outages_today"
    assert graphql_sensor.native_value == 1
    assert main_sensor.native_value == 0
    # Written when the outage starts and when it ends, not on every request.
    assert writes == [1, 1]
    assert graphql_sensor.extra_state_attributes["last_outage"].endswith(
        "s, ClientResponseError 500)"
    )


def test_the_sensors_record_no_statistics_and_stay_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / dev_probe.PROBE_DIR).mkdir()
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    hass, entry = _Hass(tmp_path), _Entry()
    asyncio.run(dev_probe.async_attach(hass, entry, updater))

    sensors = dev_probe.create_sensors(updater, 2)
    assert len(sensors) == 2
    for sensor in sensors:
        assert sensor.state_class is None
        assert sensor.entity_category == "diagnostic"
        assert sensor.unique_id.startswith("eveus2_dev_")
        # The charger never answered: the outage counters still read.
        assert sensor.available is True
        assert sensor.native_value == 0


def test_todays_figures_survive_a_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / dev_probe.PROBE_DIR
    folder.mkdir()
    today = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(dev_probe.dt_util, "now", lambda: today)
    earlier = today - timedelta(days=1)

    def line(ts: datetime, api: str, ok: bool) -> str:
        record = {"ts": ts.isoformat(), "api": api, "op": "poll", "ok": ok}
        if not ok:
            record["err"] = "TimeoutError"
        return json.dumps(record)

    start = today.replace(hour=0, minute=0, second=1)
    log = [
        line(earlier, API_MAIN, False),  # yesterday: not today's figure
        line(start, API_MAIN, False),
        line(start + timedelta(seconds=15), API_MAIN, True),
        # Home Assistant stopped during a GraphQL outage.
        line(start + timedelta(seconds=20), API_GRAPHQL, False),
        line(start + timedelta(seconds=80), API_GRAPHQL, False),
    ]
    for ts_line in log:
        month = json.loads(ts_line)["ts"][:7]
        with (folder / f"{month}.jsonl").open("a") as handle:
            handle.write(ts_line + "\n")

    charger = _Charger(_classic_main())
    updater = _updater(monkeypatch, charger)
    probe = asyncio.run(dev_probe.async_attach(_Hass(tmp_path), _Entry(), updater))

    assert probe.outages[API_MAIN].count == 1
    assert probe.outages[API_MAIN].down_seconds == 15
    graphql = probe.outages[API_GRAPHQL]
    assert graphql.count == 1
    assert graphql.down_since is None
    assert graphql.down_seconds == 60


def test_the_probe_never_stands_in_the_way_of_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    # A Home Assistant (or test double) whose config path cannot be read.
    updater = _updater(monkeypatch, _Charger(_reve_main()))
    assert asyncio.run(dev_probe.async_attach(SimpleNamespace(), _Entry(), updater)) is None


# ---------------------------------------------------------------------------
# Session comparison: GraphQL session fields next to /main's, while a car is in
# ---------------------------------------------------------------------------
# /main's session fields looked frozen on REVE (sessionTime 52745 while GraphQL
# said 0) -- or they keep the last session, as classic firmware does. One
# charge with both side by side decides which source the session sensors read.


class _SessionCharger(_Charger):
    """Answers the session document too (fields REVE answered live 2026-10-15)."""

    def post(self, url: str, **kwargs: Any) -> Any:
        if url.endswith("/graphql") and "isSessionStarted" in json.loads(kwargs["data"])["query"]:
            from test_reve_firmware import _Response

            self.calls.append(("/graphql", json.loads(kwargs["data"])))
            return _Response(200, {"data": {"stationData": {
                "stationState": self.main["state"],
                "stationSubState": self.main["subState"],
                "isSessionStarted": True,
                "chargingCommand": 1,
                "session": {"sessionTime": 600, "sessionEnergy": 1.5, "sessionCost": 6.48,
                            "energyLimit": None, "costLimit": None, "timeLimit": None},
                "measurements": {"powerMeasurement": 3600},
            }}})
        return super().post(url, **kwargs)


def _session_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: int, polls: int) -> list[dict[str, Any]]:
    (tmp_path / dev_probe.PROBE_DIR).mkdir()
    now = _clock(monkeypatch)
    main = _reve_main()
    main.update(state=state, subState=0, sessionTime=52745, sessionEnergy=2.638,
                sessionMoney=11.396, sessionStarted=1, powerMeas=3500)
    charger = _SessionCharger(main)
    updater = _updater(monkeypatch, charger)
    hass, entry = _Hass(tmp_path), _Entry()

    async def scenario() -> None:
        await dev_probe.async_attach(hass, entry, updater)
        for _ in range(polls):
            await _poll(updater)
            await _settle(hass)
            now[0] += 30  # the charging cadence
        await entry.unload()

    asyncio.run(scenario())
    return _lines(tmp_path)


def test_while_a_car_is_in_the_session_is_compared_once_a_minute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines = _session_run(tmp_path, monkeypatch, state=4, polls=4)  # two minutes
    compares = [line for line in lines if line.get("event") == "session"]

    assert len(compares) == 2
    assert compares[0]["gql"] == {
        "state": 4, "sub": 0, "started": True, "command": 1,
        "time": 600, "energy": 1.5, "cost": 6.48,
        "energy_limit": None, "cost_limit": None, "time_limit": None, "power": 3600,
    }
    assert compares[0]["main"] == {
        "state": 4, "sub": 0, "started": 1, "time": 52745, "energy": 2.638,
        "cost": 11.396, "power": 3500,
    }
    session_reads = [line for line in lines if line.get("op") == "Session"]
    assert len(session_reads) == 2 and all(line["ok"] for line in session_reads)


def test_without_a_car_the_session_is_not_asked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines = _session_run(tmp_path, monkeypatch, state=2, polls=4)
    assert not [line for line in lines if line.get("event") == "session" or line.get("op") == "Session"]
