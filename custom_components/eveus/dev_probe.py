"""Developer probe of the charger's two APIs — NOT FOR RELEASE.

Lives only on the ``reve`` branch, to collect evidence about how stable REVE
firmware's ``/main`` and GraphQL interfaces are (2026-10). It is never part of
a release: remove it first (REMOVAL below). It is also inert unless the folder
``eveus_probe`` exists in the Home Assistant configuration directory, so a
build that carried it by mistake would do nothing for anyone else.

While enabled it:

* appends one JSON line per exchange to ``eveus_probe/<YYYY-MM>.jsonl``: every
  ``/main`` poll (outcome, latency, error kind, charger state, RSSI) and every
  GraphQL request (operation, outcome, latency, error kind), plus a line when
  the probe starts and whenever the firmware versions change;
* reads REVE's GraphQL every ``GRAPHQL_PROBE_SECONDS`` instead of every five
  minutes, on a timer of its own (so independently of ``/main``'s cadence) and
  also while ``/main`` is failing, so outage windows are known to the minute;
* while a car is in, reads REVE's session from GraphQL once a minute and logs
  it next to ``/main``'s session fields from the same poll (``"event":
  "session"``), to decide which source the session sensors should read;
* adds two diagnostic sensors, one per API, counting today's outages (a run of
  failed requests ended by a success). They have no ``state_class`` (no
  long-term statistics) and change only when an outage starts or ends or the
  day turns, so their recorder rows are few and age out with
  ``purge_keep_days``.

The log is the record; ``tools/reve_probe_report.py`` turns it into daily
figures and outage windows.

REMOVAL (before REVE support is released): delete this file,
``tests/test_dev_probe.py`` and ``tools/reve_probe_report.py``, and every line
marked ``DEV-PROBE`` (``git grep -n DEV-PROBE``). Then remove the two
``*_dev_*_outages_today`` entities from the entity registry, and the
``eveus_probe`` folder once its log is archived.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval
from homeassistant.util import dt as dt_util

from . import reve
from ._payload import PayloadError
from .common_base import BaseEveusEntity
from .const import CONNECTED_STATES

_LOGGER = logging.getLogger(__name__)

PROBE_DIR = "eveus_probe"
# How often GraphQL is read while the probe runs; the integration alone reads
# it every five minutes. 30 s since 2026-10-15 22:xx, the vendor's question:
# does a 30 s interval change how often the Wi-Fi module drops (60 s before).
# A request 5 s short of the interval is due, so a poll or tick landing a
# moment early does not skip a turn.
GRAPHQL_PROBE_SECONDS = 30
# A whole number of seconds in this file (in the probe folder) replaces the
# interval when the probe starts, so a test can switch it with an entry reload.
INTERVAL_FILE = "interval_seconds"
_DUE_SLACK = 5
# The session is compared once a minute whatever the read interval is.
SESSION_COMPARE_SECONDS = 60
API_MAIN = "main"
API_GRAPHQL = "graphql"
# Read only while a car is in (CONNECTED_STATES), next to /main's session
# fields from the same poll: on REVE /main's looked frozen, or they keep the
# last session as classic firmware does. Only fields REVE answered live.
_SESSION_QUERY = """query StationData {
        stationData {
            stationState
            stationSubState
            isSessionStarted
            chargingCommand
            session {
                sessionTime
                sessionEnergy
                sessionCost
                energyLimit
                costLimit
                timeLimit
            }
            measurements {
                powerMeasurement
            }
        }
    }"""

# Every read document shares the operation name StationData; the log names
# which one it was, so a refusal shows which document the server refused.
_DOCUMENT_LABELS = {
    reve.SCHEDULES_QUERY: "ScheduleV2",
    reve.RATES_QUERY: "RatesV2",
    reve.COMMAND_QUERY: "ChargingCommand",
    _SESSION_QUERY: "Session",
}
_SENSOR_NAMES = {
    API_MAIN: "Dev /main outages today",
    API_GRAPHQL: "Dev GraphQL outages today",
}


def error_kind(err: BaseException) -> str:
    """A failure's kind: the type, the HTTP status, or the payload rule broken."""
    if isinstance(err, PayloadError):
        return f"PayloadError {err.code}"
    status = getattr(err, "status", None)
    name = type(err).__name__
    return f"{name} {status}" if isinstance(status, int) else name


class Outages:
    """Today's outages of one API: runs of failed requests ended by a success."""

    def __init__(self, today: date) -> None:
        self.day = today
        self.count = 0
        self.down_seconds = 0.0
        self.down_since: datetime | None = None
        self.last_failure: datetime | None = None
        self.last_error: str | None = None
        self.last_outage: str | None = None

    def record(self, ts: datetime, ok: bool, err: str | None) -> bool:
        """Account one request; True when what the sensor shows changed."""
        changed = self.roll(ts.date())
        if ok:
            if self.down_since is None:
                return changed
            self._close(ts)
            return True
        self.last_failure = ts
        if self.down_since is not None:
            return changed
        self.down_since = ts
        self.last_error = err
        self.count += 1
        return True

    def interrupt(self) -> bool:
        """Close an open outage at its last failure (Home Assistant restarted)."""
        if self.down_since is None or self.last_failure is None:
            return False
        self._close(self.last_failure)
        return True

    def roll(self, today: date) -> bool:
        """Start a new day; an outage in progress counts in it too."""
        if today == self.day:
            return False
        self.day = today
        self.count = 1 if self.down_since is not None else 0
        self.down_seconds = 0.0
        return True

    def attributes(self) -> dict[str, Any]:
        return {
            "down_since": None if self.down_since is None else self.down_since.isoformat(),
            "last_outage": self.last_outage,
            "down_seconds_today": round(self.down_seconds),
        }

    def _close(self, end: datetime) -> None:
        start = self.down_since
        if start is None:
            return
        midnight = datetime.combine(end.date(), datetime.min.time(), tzinfo=end.tzinfo)
        self.down_seconds += (end - max(start, midnight)).total_seconds()
        seconds = round((end - start).total_seconds())
        self.last_outage = (
            f"{start:%H:%M:%S}–{end:%H:%M:%S} ({seconds} s, {self.last_error})"
        )
        self.down_since = None


def _append(folder: Path, batch: list[tuple[str, str]]) -> None:
    for month, line in batch:
        with (folder / f"{month}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _read_interval(folder: Path) -> int:
    try:
        seconds = int((folder / INTERVAL_FILE).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return GRAPHQL_PROBE_SECONDS
    return seconds if 10 <= seconds <= 3600 else GRAPHQL_PROBE_SECONDS


def _read_day(folder: Path, day: date) -> list[dict[str, Any]]:
    path = folder / f"{day:%Y-%m}.jsonl"
    if not path.is_file():
        return []
    prefix = day.isoformat()
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            # Cheap filter first: a month holds ~130 000 lines.
            if f'"{prefix}T' in line[:40]:
                try:
                    records.append(json.loads(line))
                except ValueError:
                    continue
    return records


class DevProbe:
    """Logs a coordinator's exchanges and keeps today's outage figures."""

    def __init__(self, hass: Any, updater: Any, folder: Path) -> None:
        self._hass = hass
        self._updater = updater
        self._folder = folder
        today = dt_util.now().date()
        self.outages = {API_MAIN: Outages(today), API_GRAPHQL: Outages(today)}
        self._listeners: list[Callable[[], None]] = []
        self._buffer: list[tuple[str, str]] = []
        self._writer: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._last_graphql: float | None = None
        self._last_session: float | None = None
        self._firmware: tuple[Any, Any] | None = None
        self._unsub_midnight: Callable[[], None] | None = None
        self._unsub_reads: Callable[[], None] | None = None
        self._interval = GRAPHQL_PROBE_SECONDS

    async def async_start(self) -> None:
        today = dt_util.now().date()
        for record in await self._hass.async_add_executor_job(
            _read_day, self._folder, today
        ):
            self._replay(record)
        for outages in self.outages.values():
            outages.interrupt()
        self._interval = await self._hass.async_add_executor_job(_read_interval, self._folder)
        self._log({"event": "start", "graphql_probe_s": self._interval})
        self._wrap()
        self._unsub_midnight = async_track_time_change(
            self._hass, self._midnight, hour=0, minute=0, second=0
        )
        self._unsub_reads = async_track_time_interval(
            self._hass, self._read_tick, timedelta(seconds=self._interval)
        )

    async def async_close(self) -> None:
        if self._unsub_midnight is not None:
            self._unsub_midnight()
        if self._unsub_reads is not None:
            self._unsub_reads()
        for task in list(self._tasks):
            task.cancel()
        while self._writer is not None and not self._writer.done():
            await self._writer

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    # -- hooks on the coordinator --------------------------------------------

    def _wrap(self) -> None:
        updater = self._updater
        record_success = updater._record_success
        record_failure = updater._record_failure

        def on_success(response_time: float, new_data: dict[str, Any]) -> None:
            record_success(response_time, new_data)
            self._main_succeeded(response_time, new_data)

        def on_failure(error: Exception) -> None:
            record_failure(error)
            self._exchange(API_MAIN, "poll", False, None, error_kind(error))
            self._probe_graphql_now()

        updater._record_success = on_success
        updater._record_failure = on_failure

        reve = updater._reve
        post = reve._post
        refresh_if_due = reve.refresh_if_due

        async def logged_post(
            operation: str, document: str, variables: dict[str, Any] | None = None
        ) -> dict[str, Any]:
            label = _DOCUMENT_LABELS.get(document, operation)
            start = time.monotonic()
            self._last_graphql = start
            try:
                data = await post(operation, document, variables)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                self._exchange(
                    API_GRAPHQL, label, False, time.monotonic() - start, error_kind(err)
                )
                raise
            self._exchange(API_GRAPHQL, label, True, time.monotonic() - start, None)
            return data

        async def probing_refresh() -> None:
            if self._graphql_due():
                await reve.refresh()
            else:
                await refresh_if_due()

        reve._post = logged_post
        reve.refresh_if_due = probing_refresh

    def _graphql_due(self) -> bool:
        return (
            self._last_graphql is None
            or time.monotonic() - self._last_graphql >= self._interval - _DUE_SLACK
        )

    @callback
    def _read_tick(self, _now: datetime) -> None:
        self._probe_graphql_now()

    def _probe_graphql_now(self) -> None:
        """Ask GraphQL when due, between polls and while /main fails."""
        if not getattr(self._updater, "_reve_seen", False) or not self._graphql_due():
            return
        task = self._hass.async_create_background_task(
            self._read_if_due(), "eveus dev probe GraphQL read"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _read_if_due(self) -> None:
        # Checked again when the task runs: a poll may have read meanwhile,
        # and two reads at once are a load the charger never sees otherwise.
        if self._graphql_due():
            await self._updater._reve.refresh()

    def _main_succeeded(self, response_time: float, data: dict[str, Any]) -> None:
        firmware = (
            str(data.get("verFWMain", "")).strip() or None,
            data.get("verFWWifi"),
        )
        if firmware != self._firmware:
            self._firmware = firmware
            self._log({"event": "firmware", "main": firmware[0], "wifi": firmware[1]})
        self._exchange(
            API_MAIN, "poll", True, response_time, None,
            st=data.get("state"), sub=data.get("subState"), rssi=data.get("RSSI"),
        )
        self._compare_session_soon(data)

    def _compare_session_soon(self, data: dict[str, Any]) -> None:
        if data.get("state") not in CONNECTED_STATES or not getattr(
            self._updater, "_reve_seen", False
        ):
            return
        now = time.monotonic()
        if (
            self._last_session is not None
            and now - self._last_session < SESSION_COMPARE_SECONDS - _DUE_SLACK
        ):
            return
        self._last_session = now
        main = {
            "state": data.get("state"),
            "sub": data.get("subState"),
            "started": data.get("sessionStarted"),
            "time": data.get("sessionTime"),
            "energy": data.get("sessionEnergy"),
            "cost": data.get("sessionMoney"),
            "power": data.get("powerMeas"),
        }
        task = self._hass.async_create_background_task(
            self._compare_session(main), "eveus dev probe session read"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _compare_session(self, main: dict[str, Any]) -> None:
        try:
            data = await self._updater._reve._post("StationData", _SESSION_QUERY)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the failure is already in the log
            return
        station = data.get("stationData") if isinstance(data, dict) else None
        station = station if isinstance(station, dict) else {}
        session = station.get("session") if isinstance(station.get("session"), dict) else {}
        measurements = (
            station.get("measurements") if isinstance(station.get("measurements"), dict) else {}
        )
        gql = {
            "state": station.get("stationState"),
            "sub": station.get("stationSubState"),
            "started": station.get("isSessionStarted"),
            "command": station.get("chargingCommand"),
            "time": session.get("sessionTime"),
            "energy": session.get("sessionEnergy"),
            "cost": session.get("sessionCost"),
            "energy_limit": session.get("energyLimit"),
            "cost_limit": session.get("costLimit"),
            "time_limit": session.get("timeLimit"),
            "power": measurements.get("powerMeasurement"),
        }
        self._log({"event": "session", "gql": gql, "main": main})

    # -- records ---------------------------------------------------------------

    def _exchange(
        self,
        api: str,
        operation: str,
        ok: bool,
        seconds: float | None,
        err: str | None,
        **extra: Any,
    ) -> None:
        ts = dt_util.now()
        record: dict[str, Any] = {"api": api, "op": operation, "ok": ok}
        if seconds is not None:
            record["ms"] = round(seconds * 1000)
        if err is not None:
            record["err"] = err
        record.update({key: value for key, value in extra.items() if value is not None})
        self._log(record, ts)
        if self.outages[api].record(ts, ok, err):
            self._notify()

    def _replay(self, record: dict[str, Any]) -> None:
        if record.get("event") == "start":
            for outages in self.outages.values():
                outages.interrupt()
            return
        api = record.get("api")
        if api not in self.outages:
            return
        try:
            ts = datetime.fromisoformat(record["ts"])
        except (KeyError, TypeError, ValueError):
            return
        self.outages[api].record(ts, record.get("ok") is True, record.get("err"))

    def _log(self, record: dict[str, Any], ts: datetime | None = None) -> None:
        ts = ts or dt_util.now()
        line = json.dumps(
            {"ts": ts.isoformat(timespec="seconds"), **record},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self._buffer.append((f"{ts:%Y-%m}", line))
        if self._writer is None or self._writer.done():
            self._writer = self._hass.async_create_background_task(
                self._drain(), "eveus dev probe log"
            )

    async def _drain(self) -> None:
        # One writer at a time keeps the lines in order.
        while self._buffer:
            batch, self._buffer = self._buffer, []
            try:
                await self._hass.async_add_executor_job(_append, self._folder, batch)
            except OSError as err:
                _LOGGER.warning("Eveus dev probe cannot write its log: %s", type(err).__name__)

    @callback
    def _midnight(self, now: datetime) -> None:
        if any([outages.roll(now.date()) for outages in self.outages.values()]):
            self._notify()

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()


async def async_attach(hass: Any, entry: Any, updater: Any) -> DevProbe | None:
    """Start the probe for this coordinator if the probe folder exists."""
    try:
        folder = Path(hass.config.path(PROBE_DIR))
        enabled = await hass.async_add_executor_job(folder.is_dir)
    except Exception:  # noqa: BLE001 - the probe never stands in the way of setup
        return None
    if not enabled:
        return None
    probe = DevProbe(hass, updater, folder)
    await probe.async_start()
    updater.dev_probe = probe
    entry.async_on_unload(probe.async_close)
    _LOGGER.warning("Eveus dev probe is logging to %s", folder)
    return probe


class DevOutagesSensor(BaseEveusEntity, SensorEntity):
    """Today's outages of one of the charger's APIs (developer probe)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:lan-disconnect"

    def __init__(self, updater: Any, device_number: int, probe: DevProbe, api: str) -> None:
        self.ENTITY_NAME = _SENSOR_NAMES[api]
        super().__init__(updater, device_number)
        self._attr_translation_key = None
        self._attr_name = self.ENTITY_NAME
        self._outages = probe.outages[api]
        self._probe = probe

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> int:
        return self._outages.count

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._outages.attributes()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._probe.add_listener(self.async_write_ha_state))

    @callback
    def _handle_coordinator_update(self) -> None:
        """Written by the probe, never per poll."""


def create_sensors(updater: Any, device_number: int) -> list[DevOutagesSensor]:
    probe = getattr(updater, "dev_probe", None)
    if probe is None:
        return []
    return [
        DevOutagesSensor(updater, device_number, probe, api)
        for api in (API_MAIN, API_GRAPHQL)
    ]


__all__ = [
    "API_GRAPHQL",
    "API_MAIN",
    "GRAPHQL_PROBE_SECONDS",
    "INTERVAL_FILE",
    "PROBE_DIR",
    "DevProbe",
    "Outages",
    "async_attach",
    "create_sensors",
    "error_kind",
]
