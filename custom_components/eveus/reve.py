"""REVE firmware: where its settings really live, and what ``/main`` misreports.

REVE (first seen as REVE_PLUS_1.0.0 / 2.4.0) keeps the ``/main`` + ``/pageEvent``
API, so most of the integration works unchanged. Part of ``/main``, however, is a
projection the firmware no longer maintains — verified on a live REVE Plus on
2026-10-15:

* ``tarif*`` / ``activeTarif`` return uninitialised memory, different on every
  poll. The real tariffs are GraphQL ``ratesV2`` (a base price plus up to four
  rates with weekdays), in currency units rather than hundredths.
* ``sh1*`` / ``sh2*`` mirror a legacy copy of the schedules. Writing them changes
  ``/main`` but not the schedule the charger runs, and ``sh*Enabled`` reverts
  within seconds. The real schedules are GraphQL ``scheduleV2``.
* ``currentSet`` written through ``/pageEvent`` reverts within a second; GraphQL
  ``stationCurrent`` holds (and ``/main`` reports it).
* ``evseEnabled`` — the classic Stop flag — changes nothing visible: on classic
  firmware Stop put an idle charger in subState 1, on REVE ``evseEnabled=1``
  leaves it at 0. REVE starts and stops through GraphQL ``chargingCommand``
  (the vendor app's only start/stop): 2 puts the charger in subState 9,
  waiting for activation, until 1 (start) or 0 (charge now) releases it.
* ``leakValue`` / ``leakValueH`` are raw ADC counts — the same numbers ``/debug``
  shows as ``cll`` / ``clh`` — not milliamps.
* ``temperature2`` reads -60, where the same charger on classic firmware
  reported a real plug temperature.

Everything else written through ``/pageEvent`` (One Charge, adaptive mode and
threshold, minimum voltage) reaches the charger's real state.

So the coordinator, for a REVE charger only, drops the unreliable fields, lays
the GraphQL schedules and rates over the legacy keys the entities already read,
and marks every field left without a source unsupported. The entities, their
unique IDs and every other firmware's payload stay exactly as they were.

The GraphQL documents below are copied from the charger maker's own app, and
no query text is ever built at runtime. The server silently drops a field it
does not know (checked 2026-10-16), so only fields of the app's own documents
are asked for. Its HTTP 500 windows of up to ~18 minutes follow the charger's
Wi-Fi reconnects, which happen only while it has internet access.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import partial
from typing import Any, Final

import aiohttp

from .client import GraphQLError, post_graphql
from .const import MAX_VALID_SYSTEM_TIME
from .reve_fields import station_to_main
from .snapshot import FIELD_KEYS

_LOGGER = logging.getLogger(__name__)

FAMILY_CLASSIC: Final = "classic"
FAMILY_REVE: Final = "reve"

# /main fields REVE reports but no longer maintains (see the module docstring).
REVE_UNRELIABLE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "tarif", "activeTarif",
        "tarifAEnable", "tarifAValue", "tarifAStart", "tarifAStop",
        "tarifBEnable", "tarifBValue", "tarifBStart", "tarifBStop",
        "leakValue", "leakValueH",
        # Stop is read from chargingCommand instead; see the docstring.
        "evseEnabled",
        *(
            f"sh{slot}{field}"
            for slot in (1, 2)
            for field in (
                "Enabled", "Start", "Stop", "CurrentEnable", "CurrentValue",
                "EnergyEnable", "EnergyValue",
            )
        ),
    }
)
# temperature2 as REVE reports it; the same charger on classic firmware
# reported a real plug temperature there.
_NO_PLUG_SENSOR: Final = -60
# REVE rates that have entities of their own (Rate 2 and Rate 3).
_RATE_SLOTS: Final = 2

# Legacy schedule key suffix -> scheduleV2 item field.
_SLOT_FIELDS: Final[dict[str, str]] = {
    "Enabled": "enabled",
    "Start": "start",
    "Stop": "end",
    "CurrentEnable": "currentLimitEnabled",
    "CurrentValue": "value",
}
SCHEDULE_COMMANDS: Final[frozenset[str]] = frozenset(
    f"sh{slot}{suffix}" for slot in (1, 2) for suffix in _SLOT_FIELDS
)
# scheduleV2 has no per-schedule energy cap.
SCHEDULE_ENERGY_COMMANDS: Final[frozenset[str]] = frozenset(
    f"sh{slot}Energy{suffix}" for slot in (1, 2) for suffix in ("Enable", "Value")
)
CURRENT_COMMAND: Final = "currentSet"
STOP_COMMAND: Final = "evseEnabled"
# chargingCommand values, as the vendor app sends them.
_COMMAND_START: Final = 1
_COMMAND_STOP: Final = 2
_CHARGING_COMMANDS: Final = frozenset({0, _COMMAND_START, _COMMAND_STOP})

_MINUTES_PER_DAY: Final = 1440

# Settings written through GraphQL updateStationData, in the shape the
# maker's apps send them: the local app's generic toggle sends 0/1 (PE
# control), its dedicated switches a boolean (One Charge), a list its number.
_FLAG_WRITES: Final[dict[str, str]] = {"oneCharge": "isOneChargeEnabled"}
_TOGGLE_WRITES: Final[dict[str, str]] = {"groundCtrl": "isGroundConnected"}
_NUMBER_WRITES: Final[dict[str, str]] = {"aiVoltage": "voltageThreshold", "minVoltage": "minVoltage"}
# Adaptive mode: the integration's 0 Off, 1 Voltage, 2 Auto (percent drop);
# REVE has no 3 Power.
_ADAPTIVE_MODES: Final[frozenset[int]] = frozenset({0, 1, 2})
# Session limits: the /main number key, its on/off key, the GraphQL session
# field, and the factor from /main read units to write units (the energy
# limit reads in kWh and is written in Wh, as /pageEvent takes it too).
_LIMIT_WRITES: Final[tuple[tuple[str, str, str, int], ...]] = (
    ("energyLimit", "energyLimitS", "energyLimit", 1000),
    ("timeLimit", "timeLimitS", "timeLimit", 1),
    ("moneyLimit", "moneyLimitS", "costLimit", 1),
)
# Still written through /pageEvent on REVE until decided: the limits' master
# switch (REVE has none), OCPP (its write carries the whole object, auth key
# included) and the counter resets (irreversible, untested live).
PAGE_EVENT_COMMANDS: Final[frozenset[str]] = frozenset(
    {"suspendLimits", "ocppEnabled", "rstEM1", "rstEM2"}
)

# How often the schedules, rates and Stop are re-read, how long a failed read
# waits before the next attempt, and how long a last good read stays in force.
# They are settings: they change in the app rarely, and a write from Home
# Assistant updates the cached read at once. REVE's GraphQL server is fragile —
# with only Home Assistant polling it stopped answering for ~20, ~38 and ~5
# minutes within two hours (2026-10-15) — so it is read sparingly, and a last
# good read stands through such a window instead of flapping every entity it
# feeds to unavailable. A server silent for an hour makes them unavailable.
EXTRAS_REFRESH_SECONDS: Final = 300
EXTRAS_RETRY_SECONDS: Final = 300
EXTRAS_MAX_AGE_SECONDS: Final = 3600

# The vendor app reads each setting with its own small document; these are
# its ScheduleV2 and RatesV2 queries as the local interface takes them (no
# stationId), and a document of the same shape for chargingCommand. Sent one
# at a time, in this order, stopping at the first refused one.
SCHEDULES_QUERY: Final = """query StationData {
        stationData {
            scheduleV2 {
                name
                start
                end
                weekdays
                enabled
                value
                currentLimitEnabled
            }
        }
    }"""

RATES_QUERY: Final = """query StationData {
        stationData {
            ratesV2 {
                base
                rates {
                    name
                    start
                    end
                    weekdays
                    enabled
                    value
                }
            }
        }
    }"""

COMMAND_QUERY: Final = """query StationData {
        stationData {
            chargingCommand
        }
    }"""

# Once a charger is known to be REVE, every poll reads its measurements with
# this document: the charger app's own StationData, cut down to the fields
# reve_fields maps to /main keys, plus chargingCommand so Stop shows at once.
# /main is read only when it does not answer (MEASUREMENTS_RETRY_SECONDS keeps
# a server that times out from delaying every poll by its whole timeout).
MEASUREMENTS_QUERY: Final = """query StationData {
        stationData {
            designedCurrent
            minCurrent
            mainFWVersion
            wifiFWVersion
            pilotStatus
            stationState
            stationSubState
            isSessionStarted
            stationCurrent
            gridRange
            minVoltage
            RSSI
            chargerType
            releType
            adaptiveModeStatus
            voltageThreshold
            ground
            isGroundConnected
            timeSettings {
                timeZone
                systemTime
            }
            isChargerEnabled
            isSuspendError
            isOneChargeEnabled
            totalEnergy
            session {
                sessionTime
                sessionEnergy
                sessionCost
                energyLimit
                costLimit
                timeLimit
            }
            ocpp {
                ocppEnabled
                stationId
            }
            iem {
                IEM1
                IEM2
                IEM1_money
                IEM2_money
            }
            measurements {
                batteryVoltage
                curMeasurement1
                curMeasurement2
                curMeasurement3
                voltMeasurement1
                voltMeasurement2
                voltMeasurement3
                powerMeasurement
                temperature {
                    temperature1
                    temperature2
                }
            }
            language
            STA_IP_Addres
            chargingCommand
        }
    }"""
MEASUREMENTS_RETRY_SECONDS: Final = 60

# What only /main carries on REVE, kept from the last /main read between
# GraphQL polls: the device-registry strings (for as long as the integration
# runs) and the few entity fields GraphQL has no place for (refreshed every
# five minutes, dropped after an hour without /main). A limit value GraphQL
# reports wins; /main's stands in while GraphQL reports the limit off.
MAIN_IDENTITY_FIELDS: Final[frozenset[str]] = frozenset({"manufacturer", "model", "serialNum"})
MAIN_ONLY_FIELDS: Final[frozenset[str]] = frozenset(
    {"suspendLimits", "aiModecurrent", "energyLimit", "moneyLimit", "timeLimit"}
)
MAIN_RESIDUE_REFRESH_SECONDS: Final = 300
MAIN_RESIDUE_MAX_AGE_SECONDS: Final = 3600

# /main reports the session frozen from the old firmware (sessionTime 52745
# on an idle charger); GraphQL reports it live. A poll served by /main shows
# the last GraphQL session for a few minutes, then none.
SESSION_FIELDS: Final = ("sessionTime", "sessionEnergy", "sessionMoney")
SESSION_HOLD_SECONDS: Final = 300

UPDATE_MUTATION: Final = """mutation UpdateStationData($input: StationDataInput!) {
        updateStationData(input: $input)
    }"""


class ReveCommandUnsupported(Exception):
    """A setting REVE has no place for (or a schedule slot it does not have)."""


def is_reve_payload(data: Mapping[str, Any]) -> bool:
    """Whether a ``/main`` payload comes from REVE firmware.

    Two explicit markers, either sufficient: the version string the app shows
    (``verFWWifi``, ``REVE_PLUS_1.0.0``) and the maker name. Anything else —
    including a payload with neither field — is treated as it always was.
    """
    wifi = data.get("verFWWifi")
    if isinstance(wifi, str) and wifi.strip().upper().startswith("REVE_"):
        return True
    maker = data.get("manufacturer")
    return isinstance(maker, str) and maker.strip().lower() == "reve"


# ---------------------------------------------------------------------------
# GraphQL data
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReveExtras:
    """The schedules and tariffs REVE keeps only in GraphQL, validated."""

    schedules: tuple[dict[str, Any], ...]
    base_rate: float
    rates: tuple[dict[str, Any], ...]
    # The last start/stop command; None when REVE did not report a usable one.
    charging_command: int | None = None


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1) and not isinstance(value, float):
        return bool(value)
    raise ValueError("not a boolean")  # pragma: no mutate - message text; only the exception type is ever logged


def _number(value: Any) -> int | float:
    """A finite, non-negative number; whole numbers stay ``int`` on the wire."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("not a number")  # pragma: no mutate - message text; only the exception type is ever logged
    if not math.isfinite(value) or value < 0:
        raise ValueError("number out of range")  # pragma: no mutate - message text; only the exception type is ever logged
    return int(value) if float(value).is_integer() else float(value)


def _minute(value: Any) -> int:
    number = _number(value)
    if not isinstance(number, int) or number >= _MINUTES_PER_DAY:
        raise ValueError("minute of day out of range")  # pragma: no mutate - message text; only the exception type is ever logged
    return number


def _whole(value: Any) -> int:
    number = _number(value)
    if not isinstance(number, int):
        raise ValueError("not a whole number")  # pragma: no mutate - message text; only the exception type is ever logged
    return number


def _weekdays(value: Any) -> list[int]:
    """Weekdays as the app reads them: a 0-6 list (0 = Sunday) or a bitmask."""
    if isinstance(value, list):
        days = {_whole(day) for day in value}
        if any(day > 6 for day in days):
            raise ValueError("weekday out of range")  # pragma: no mutate - message text; only the exception type is ever logged
        return sorted(days)
    mask = _whole(value)
    if mask > 127:
        raise ValueError("weekday mask out of range")  # pragma: no mutate - message text; only the exception type is ever logged
    return [day for day in range(7) if mask & (1 << day)]  # pragma: no mutate - a mask of at most 127 has no bit 7


def _limit_reading(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value


def _toggle(value: Any) -> int:
    """0/1, as the app's generic toggle sends it."""
    return int(_bool(value))


def _signed_whole(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("not a number")  # pragma: no mutate - message text; only the exception type is ever logged
    if not math.isfinite(value) or not float(value).is_integer():
        raise ValueError("not a whole number")  # pragma: no mutate - message text; only the exception type is ever logged
    return int(value)


def _adaptive_mode(value: Any) -> int:
    mode = _whole(value)
    if mode not in _ADAPTIVE_MODES:
        raise ReveCommandUnsupported(f"adaptive mode {mode}")  # pragma: no mutate - message text; only the exception type is ever logged
    return mode


def _name(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("name is not text")  # pragma: no mutate - message text; only the exception type is ever logged
    return value


def _schedule_item(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("schedule is not an object")  # pragma: no mutate - message text; only the exception type is ever logged
    return {
        "name": _name(raw.get("name")),
        "start": _minute(raw.get("start")),
        "end": _minute(raw.get("end")),
        "weekdays": _weekdays(raw.get("weekdays")),
        "enabled": _bool(raw.get("enabled")),
        "value": _number(raw.get("value")),
        "currentLimitEnabled": _bool(raw.get("currentLimitEnabled")),
    }


def _rate_item(raw: Any) -> dict[str, Any]:
    """The fields the tariff sensors read. Names are not read and weekdays that
    cannot be read become ``None``, so a format change in them cannot take the
    tariffs away; only the active rate then stays unknown."""
    if not isinstance(raw, dict):
        raise ValueError("rate is not an object")  # pragma: no mutate - message text; only the exception type is ever logged
    try:
        weekdays: list[int] | None = _weekdays(raw.get("weekdays"))
    except (TypeError, ValueError):
        weekdays = None
    return {
        "start": _minute(raw.get("start")),
        "end": _minute(raw.get("end")),
        "enabled": _bool(raw.get("enabled")),
        "value": _number(raw.get("value")),
        "weekdays": weekdays,
    }


def parse_schedules(station: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Validate ``stationData.scheduleV2``; any doubt is a ``ValueError``."""
    schedules = station.get("scheduleV2")
    if not isinstance(schedules, list):
        raise ValueError("no scheduleV2")  # pragma: no mutate - message text; only the exception type is ever logged
    return tuple(_schedule_item(item) for item in schedules)


def parse_extras(data: Any) -> ReveExtras:
    """Validate the merged ``data`` of the three read documents; any doubt is a
    ``ValueError``."""
    station = data.get("stationData") if isinstance(data, dict) else None
    if not isinstance(station, dict):
        raise ValueError("no stationData")  # pragma: no mutate - message text; only the exception type is ever logged
    schedules = parse_schedules(station)
    rates = station.get("ratesV2")
    if not isinstance(rates, dict):
        raise ValueError("no ratesV2")  # pragma: no mutate - message text; only the exception type is ever logged
    rate_items = rates.get("rates")
    if not isinstance(rate_items, list):
        raise ValueError("no rates")  # pragma: no mutate - message text; only the exception type is ever logged
    command = station.get("chargingCommand")
    return ReveExtras(
        schedules=schedules,
        base_rate=_number(rates.get("base")),
        rates=tuple(_rate_item(item) for item in rate_items),
        # Stop alone becomes unsupported without it; the schedules and rates
        # are still good.
        charging_command=(
            command
            if type(command) is int and command in _CHARGING_COMMANDS
            else None
        ),
    )


# ---------------------------------------------------------------------------
# /main normalisation
# ---------------------------------------------------------------------------


def _hundredths(value: float) -> int:
    """Currency units as the legacy ``tarif*`` hundredths."""
    return round(value * 100)


def _schedule_projection(schedules: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    projection: dict[str, Any] = {}
    for slot, item in enumerate(schedules[:2], start=1):
        projection[f"sh{slot}Enabled"] = int(item["enabled"])
        projection[f"sh{slot}Start"] = item["start"]
        projection[f"sh{slot}Stop"] = item["end"]
        projection[f"sh{slot}CurrentEnable"] = int(item["currentLimitEnabled"])
        projection[f"sh{slot}CurrentValue"] = item["value"]
    return projection


def _rate_projection(extras: ReveExtras) -> dict[str, Any]:
    projection: dict[str, Any] = {"tarif": _hundredths(extras.base_rate)}
    for letter, item in zip("AB", extras.rates):
        projection[f"tarif{letter}Enable"] = int(item["enabled"])
        projection[f"tarif{letter}Value"] = _hundredths(item["value"])
        projection[f"tarif{letter}Start"] = item["start"]
        projection[f"tarif{letter}Stop"] = item["end"]
    return projection


def _wall_clock(main: Mapping[str, Any]) -> int | None:
    """The charger's wall clock (``systemTime``, shifted by ``timeZone``)."""
    clock = main.get("systemTime")
    if main.get("timeZone") is None or isinstance(clock, bool) or not isinstance(clock, int):
        return None
    return clock if 0 < clock < MAX_VALID_SYSTEM_TIME else None


def active_rate_index(rates: tuple[dict[str, Any], ...], clock: int) -> int | None:
    """``activeTarif`` for REVE, which keeps none: 0 the base price, 1 and 2
    the first and second rate.

    The first enabled rate whose window holds the clock's minute, on one of its
    weekdays, applies; a window past midnight belongs to the day it started.
    ``None`` when that cannot be known or has no entity: a zero-length window,
    unreadable weekdays, or a third or fourth rate.
    """
    day, second = divmod(clock, 86400)
    minute = second // 60  # pragma: no mutate - window edges are whole minutes, so a fractional minute compares the same
    weekday = (day + 4) % 7  # 1970-01-01 was a Thursday; 0 = Sunday, as REVE counts
    for index, item in enumerate(rates, start=1):
        if not item["enabled"]:
            continue
        start, end = item["start"], item["end"]
        if start == end or item["weekdays"] is None:
            return None
        if start < end:  # pragma: no mutate - start == end returned above
            started = weekday if start <= minute < end else None
        elif minute >= start:
            started = weekday
        else:
            started = (weekday - 1) % 7 if minute < end else None
        if started is None or started not in item["weekdays"]:
            continue
        return index if index <= _RATE_SLOTS else None
    return 0


def _is_no_sensor_reading(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value == _NO_PLUG_SENSOR
    )


def normalize_reve_payload(
    main: Mapping[str, Any], extras: ReveExtras | None
) -> tuple[dict[str, Any], frozenset[str]]:
    """Return the payload the entities read, and the fields REVE cannot provide.

    The input is never mutated. Without ``extras`` (GraphQL not read yet, or no
    longer answering) the schedule and tariff fields are simply unsupported, so
    their entities are unavailable rather than showing ``/main``'s projection.
    """
    payload = {key: value for key, value in main.items() if key not in REVE_UNRELIABLE_FIELDS}
    if _is_no_sensor_reading(payload.get("temperature2")):
        del payload["temperature2"]
    if extras is not None:
        payload.update(_schedule_projection(extras.schedules))
        payload.update(_rate_projection(extras))
        clock = _wall_clock(main)
        active = None if clock is None else active_rate_index(extras.rates, clock)
        if active is not None:
            payload["activeTarif"] = active
        if extras.charging_command is not None:
            payload[STOP_COMMAND] = int(extras.charging_command == _COMMAND_STOP)
    unsupported = frozenset(key for key in FIELD_KEYS if key not in payload)
    return payload, unsupported


# ---------------------------------------------------------------------------
# Schedule writes
# ---------------------------------------------------------------------------


def _coerce_slot_value(field: str, value: Any) -> Any:
    if field in ("enabled", "currentLimitEnabled"):
        return _bool(value)
    if field in ("start", "end"):
        return _minute(value)
    return _whole(value)


def apply_schedule_command(
    schedules: list[dict[str, Any]], command: str, value: Any
) -> list[dict[str, Any]]:
    """The full ``scheduleV2`` list with one legacy schedule field changed.

    REVE replaces the whole list on every write, so every other schedule and
    every other field goes back exactly as it was read.
    """
    if command not in SCHEDULE_COMMANDS:
        raise ReveCommandUnsupported(command)  # pragma: no mutate - message text; only the exception type is ever logged
    slot = int(command[2])
    if len(schedules) < slot:
        raise ReveCommandUnsupported(f"schedule {slot} does not exist")  # pragma: no mutate - message text; only the exception type is ever logged
    field = _SLOT_FIELDS[command[3:]]
    updated = [dict(item) for item in schedules]
    updated[slot - 1][field] = _coerce_slot_value(field, value)
    return updated


# ---------------------------------------------------------------------------
# The coordinator's GraphQL side
# ---------------------------------------------------------------------------


class ReveApi:
    """Reads and writes the settings REVE keeps only in GraphQL."""

    def __init__(self, updater: Any) -> None:
        self._updater = updater
        self._extras: ReveExtras | None = None  # pragma: no mutate - read only behind `_fetched_at`, which is None here
        self._fetched_at: float | None = None
        self._retry_at = 0.0  # pragma: no mutate - any start below the monotonic clock is equivalent
        self._failures = 0
        self._last_error: str | None = None  # pragma: no mutate - only read once a read has set or cleared it
        self._measurements_retry_at = 0.0  # pragma: no mutate - any start below the monotonic clock is equivalent
        self._measurement_reads = 0
        self._measurement_failures = 0
        self._main_fallbacks = 0
        self._last_source: str | None = None
        self._residue: dict[str, Any] = {}
        self._residue_at: float | None = None
        self._residue_tried_at: float | None = None
        self._session: tuple[dict[str, Any], float] | None = None
        # A limit's value in /main read units, kept while the limit is off
        # (REVE then reports null) so its number shows what switching it back
        # on would apply.
        self._limit_values: dict[str, int | float] = {}

    @property
    def extras(self) -> ReveExtras | None:
        """The last good read, while it is recent enough to stand for now."""
        if self._extras is None or self._fetched_at is None:
            return None
        if time.monotonic() - self._fetched_at > EXTRAS_MAX_AGE_SECONDS:
            return None
        return self._extras

    async def refresh_if_due(self) -> None:
        """Re-read schedules and rates when due. Never raises: ``/main`` decides
        whether a poll succeeded, and a failed read only waits for its retry."""
        now = time.monotonic()
        if self._fetched_at is not None and now - self._fetched_at < EXTRAS_REFRESH_SECONDS:
            return
        if now < self._retry_at:
            return
        await self.refresh()

    async def refresh(self) -> None:
        """Re-read schedules and rates now; a failure only schedules the retry."""
        now = time.monotonic()
        try:
            self._store(await self._read_extras())
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            self._failures += 1
            self._last_error = type(err).__name__
            self._retry_at = now + EXTRAS_RETRY_SECONDS
            _LOGGER.debug("REVE schedules/rates read failed: %s", type(err).__name__)

    # -- measurements --------------------------------------------------------

    def measurements_due(self) -> bool:
        """Whether this poll should try GraphQL (not while backing off)."""
        return time.monotonic() >= self._measurements_retry_at

    async def read_measurements(
        self, validate: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """This poll's measurements in ``/main`` keys. Raises when GraphQL gives
        no usable reply, or one ``validate`` (the ``/main`` rules) rejects."""
        try:
            station = await self._station(MEASUREMENTS_QUERY)
            main = station_to_main(station)
            if "state" not in main:
                raise ValueError("no stationState")  # pragma: no mutate - message text; only the exception type is ever logged
            if validate is not None:
                main = validate(main)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            self._measurement_failures += 1
            self._last_error = type(err).__name__
            self._measurements_retry_at = time.monotonic() + MEASUREMENTS_RETRY_SECONDS
            _LOGGER.debug("REVE measurements read failed: %s", type(err).__name__)
            raise
        self._measurement_reads += 1
        self._last_error = None
        self._last_source = "graphql"
        self._session = (
            {key: main[key] for key in SESSION_FIELDS if key in main},
            time.monotonic(),
        )
        self._note_charging_command(station.get("chargingCommand"))
        return main

    def with_main_residue(self, measurements: Mapping[str, Any]) -> dict[str, Any]:
        """The measurements with what only ``/main`` carries laid under them,
        and a limit that is off showing the value it was last set to."""
        payload = dict(measurements)
        for key, _flag, _field, _factor in _LIMIT_WRITES:
            if key in payload:
                self._limit_values[key] = payload[key]
            elif key in self._limit_values:
                payload[key] = self._limit_values[key]
        fresh = self._residue_fresh(time.monotonic())
        for key, value in self._residue.items():
            if key in MAIN_IDENTITY_FIELDS or fresh:
                payload.setdefault(key, value)
        return payload

    @property
    def last_source(self) -> str | None:
        """Which API answered the last poll: ``graphql`` or ``main``."""
        return self._last_source

    def main_residue_due(self) -> bool:
        """Whether a GraphQL poll should also read ``/main`` for its own fields:
        every five minutes, whether or not the last try found ``/main``."""
        tried = self._residue_tried_at
        return tried is None or time.monotonic() - tried >= MAIN_RESIDUE_REFRESH_SECONDS

    def main_residue_tried(self) -> None:
        self._residue_tried_at = time.monotonic()

    def keep_main_residue(self, main: Mapping[str, Any]) -> None:
        """Keep what only ``/main`` carries from a ``/main`` reply."""
        self._residue = {
            key: main[key]
            for key in MAIN_IDENTITY_FIELDS | MAIN_ONLY_FIELDS
            if key in main
        }
        self._residue_at = self._residue_tried_at = time.monotonic()

    def from_main(self, main: Mapping[str, Any], *, fallback: bool) -> dict[str, Any]:
        """A REVE ``/main`` payload as a poll shows it: the frozen session
        replaced by the last GraphQL one while that is recent, else left out."""
        self.keep_main_residue(main)
        self._last_source = "main"
        if fallback:
            self._main_fallbacks += 1
        payload = {key: value for key, value in main.items() if key not in SESSION_FIELDS}
        held = self._session
        if held is not None and time.monotonic() - held[1] <= SESSION_HOLD_SECONDS:
            payload.update(held[0])
        return payload

    def _residue_fresh(self, now: float) -> bool:
        return self._residue_at is not None and now - self._residue_at <= MAIN_RESIDUE_MAX_AGE_SECONDS

    def _note_charging_command(self, command: Any) -> None:
        extras = self._extras
        if extras is None or type(command) is not int or command not in _CHARGING_COMMANDS:
            return
        self._extras = replace(extras, charging_command=command)

    def route(self, command: str) -> Callable[[Any], Awaitable[None]] | None:
        """The GraphQL write for a command, or ``None`` to keep ``/pageEvent``."""
        if command == CURRENT_COMMAND:
            return self.set_station_current
        if command == STOP_COMMAND:
            return self.set_stop
        if command in SCHEDULE_COMMANDS:
            return partial(self.write_schedule, command)
        if command in SCHEDULE_ENERGY_COMMANDS:
            return partial(self._refuse, command)  # pragma: no mutate - the command only names the refusal
        if command in _FLAG_WRITES:
            return partial(self._write_value, _FLAG_WRITES[command], _bool)
        if command in _TOGGLE_WRITES:
            return partial(self._write_value, _TOGGLE_WRITES[command], _toggle)
        if command in _NUMBER_WRITES:
            return partial(self._write_value, _NUMBER_WRITES[command], _whole)
        if command == "aiMode":
            return partial(self._write_value, "adaptiveModeStatus", _adaptive_mode)
        if command == "timeZone":
            return self.set_time_zone
        if command == "systemTime":
            return self.set_system_time
        for key, flag, field, factor in _LIMIT_WRITES:
            if command == key:
                return partial(self.set_limit, key, flag, field, factor)
            if command == flag:
                return partial(self.switch_limit, key, flag, field, factor)
        return None

    async def _write_value(self, field: str, coerce: Callable[[Any], Any], value: Any) -> None:
        await self._mutate({field: coerce(value)})

    async def set_time_zone(self, value: Any) -> None:
        await self._mutate({"timeSettings": {"timeZone": _signed_whole(value)}})

    async def set_system_time(self, value: Any) -> None:
        """Set the clock to a UTC instant (seconds), as the app sends it: ISO text."""
        instant = datetime.fromtimestamp(_whole(value), tz=timezone.utc)
        await self._mutate({"timeSettings": {"systemTime": instant.strftime("%Y-%m-%dT%H:%M:%SZ")}})

    async def set_limit(self, key: str, flag: str, field: str, factor: int, value: Any) -> None:
        """A limit's value (write units). REVE has no on/off flag: writing a
        value switches the limit on, so while it is off the value is only kept
        for when it is switched on."""
        written = _whole(value)
        if self._updater.snapshot.get_int(flag) == 1:
            await self._mutate({"session": {field: written}})
        self._limit_values[key] = written / factor if factor != 1 else written

    async def switch_limit(self, key: str, flag: str, field: str, factor: int, value: Any) -> None:
        """Switch a limit on with its kept (or current) value, or off: ``null``."""
        if not _bool(value):
            current = _limit_reading(self._updater.snapshot.get(key))
            if current is not None and current > 0:
                self._limit_values.setdefault(key, current)
            await self._mutate({"session": {field: None}})
            return
        read = self._limit_values.get(key)
        if read is None:
            read = _limit_reading(self._updater.snapshot.get(key))
        if read is None or read <= 0:
            raise ReveCommandUnsupported(f"{key} has no value to switch on with")  # pragma: no mutate - message text; only the exception type is ever logged
        await self._mutate({"session": {field: round(read * factor)}})

    async def set_station_current(self, value: Any) -> None:
        await self._mutate({"stationCurrent": _whole(value)})

    async def set_stop(self, value: Any) -> None:
        """Stop (1) or release (0) charging the way the vendor app does."""
        command = _COMMAND_STOP if _bool(value) else _COMMAND_START
        await self._mutate({"chargingCommand": command})
        extras = self._extras
        if extras is not None:
            self._store(
                ReveExtras(extras.schedules, extras.base_rate, extras.rates, command)
            )

    async def write_schedule(self, command: str, value: Any) -> None:
        # Read right before writing: the list is replaced whole, and the app
        # may have changed another schedule since the last poll. The rates and
        # Stop stand on the last read; with none yet, read everything.
        held = self._extras
        if held is None:
            current = await self._read_extras()
        else:
            schedules = parse_schedules(await self._station(SCHEDULES_QUERY))
            current = ReveExtras(schedules, held.base_rate, held.rates, held.charging_command)
        updated = apply_schedule_command(list(current.schedules), command, value)
        await self._mutate({"scheduleV2": updated})
        self._store(
            ReveExtras(
                tuple(updated), current.base_rate, current.rates, current.charging_command
            )
        )

    async def _refuse(self, command: str, _value: Any) -> None:
        raise ReveCommandUnsupported(command)  # pragma: no mutate - message text; only the exception type is ever logged

    @property
    def answering(self) -> bool | None:
        """Whether the last GraphQL read succeeded; ``None`` before the first."""
        if self._last_error is not None:
            return False
        return None if self._fetched_at is None else True

    def diagnostics(self) -> dict[str, Any]:
        extras = self.extras
        return {
            "graphql_ok": extras is not None and self._last_error is None,
            "schedules": None if extras is None else len(extras.schedules),
            "rates": None if extras is None else len(extras.rates),
            "read_age_seconds": (
                None
                if self._fetched_at is None
                else round(time.monotonic() - self._fetched_at)
            ),
            "read_failures": self._failures,
            "last_error": self._last_error,
            "measurements": {
                "graphql_reads": self._measurement_reads,
                "graphql_failures": self._measurement_failures,
                "main_fallbacks": self._main_fallbacks,
                "last_source": self._last_source,
                "main_residue_age_seconds": (
                    None
                    if self._residue_at is None
                    else round(time.monotonic() - self._residue_at)
                ),
            },
        }

    def _store(self, extras: ReveExtras) -> None:
        self._extras = extras
        self._fetched_at = time.monotonic()
        self._retry_at = 0.0  # pragma: no mutate - any value below the monotonic clock is equivalent
        self._last_error = None

    async def _read_extras(self) -> ReveExtras:
        station: dict[str, Any] = {}
        for document in (SCHEDULES_QUERY, RATES_QUERY, COMMAND_QUERY):
            station.update(await self._station(document))
        return parse_extras({"stationData": station})

    async def _station(self, document: str) -> dict[str, Any]:
        data = await self._post("StationData", document)
        station = data.get("stationData")
        if not isinstance(station, dict):
            raise ValueError("no stationData")  # pragma: no mutate - message text; only the exception type is ever logged
        return station

    async def _mutate(self, change: dict[str, Any]) -> None:
        data = await self._post("UpdateStationData", UPDATE_MUTATION, {"input": change})
        result = data.get("updateStationData")
        accepted = result is True or (
            isinstance(result, dict) and result.get("success") is True
        )
        if not accepted:
            raise GraphQLError("REVE did not accept the change")  # pragma: no mutate - message text; only the exception type is ever logged

    async def _post(
        self, operation: str, document: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        updater = self._updater
        return await post_graphql(
            updater.get_session(),
            updater.url_for("/graphql"),
            auth=updater.basic_auth,
            operation=operation,
            document=document,
            variables=variables,
        )
