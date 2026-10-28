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
* One Charge is ``chargingCommand`` 0, "charge now": the app's Start outside a
  schedule window (verified live 2026-10-17). The station's own
  ``isOneChargeEnabled`` flag is not used; it was never seen starting a charge.
  The session then runs in manual mode: the schedules and every limit are
  ignored until the car is unplugged.

Everything else written through ``/pageEvent`` (adaptive mode and threshold,
minimum voltage) reaches the charger's real state.

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

from .charge_reason import reached_limit
from .client import GraphQLError, post_graphql
from .const import (
    CONNECTED_STATES,
    DEVICE_STATE_CHARGING,
    DEVICE_STATE_COMPLETE,
    DEVICE_STATE_STANDBY,
    MAX_VALID_SYSTEM_TIME,
    PLUG_UNKNOWN_STATES,
    SESSION_ACTIVE_STATES,
)
from .reve_fields import graphql_paths, station_to_main
from .snapshot import FIELD_KEYS
from .utils import get_safe_value

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
        # Disable all: REVE drops a write within seconds (measured 18.10).
        "suspendLimits",
        # Stop and One Charge are read from chargingCommand instead; see the
        # docstring.
        "evseEnabled", "oneCharge",
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
# A schedule's days of the week: no entity of their own, written from the
# card (a 0-6 list, 0 = Sunday, as scheduleV2 keeps them).
SCHEDULE_WEEKDAY_COMMANDS: Final[frozenset[str]] = frozenset(
    f"sh{slot}Weekdays" for slot in (1, 2)
)
# scheduleV2 has no per-schedule energy cap.
SCHEDULE_ENERGY_COMMANDS: Final[frozenset[str]] = frozenset(
    f"sh{slot}Energy{suffix}" for slot in (1, 2) for suffix in ("Enable", "Value")
)
CURRENT_COMMAND: Final = "currentSet"
STOP_COMMAND: Final = "evseEnabled"
ONE_CHARGE_COMMAND: Final = "oneCharge"
# chargingCommand values, as the vendor app sends them.
_COMMAND_CHARGE_NOW: Final = 0
_COMMAND_START: Final = 1
_COMMAND_STOP: Final = 2
_CHARGING_COMMANDS: Final = frozenset({_COMMAND_CHARGE_NOW, _COMMAND_START, _COMMAND_STOP})

_MINUTES_PER_DAY: Final = 1440
# How far the station's session clock may trail Home Assistant's before a
# restored manual mode is taken to belong to an older session.
_RESTORE_SLACK_SECONDS: Final = 60

# Settings written through GraphQL updateStationData, in the shape the
# maker's apps send them: the local app's generic toggle sends 0/1 (PE
# control), a list its number.
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
# Counter resets as the app sends them: the counter's own field set to 1
# (Counter A checked live 2026-10-17; Counter B follows the same shape and has
# not been reset on a charger yet).
_RESET_WRITES: Final[dict[str, str]] = {"rstEM1": "IEM1", "rstEM2": "IEM2"}
# Still written through /pageEvent on REVE until decided: OCPP (its write
# carries the whole object, auth key included).
PAGE_EVENT_COMMANDS: Final[frozenset[str]] = frozenset({"ocppEnabled"})
# The limits' master switch: REVE has none, and drops a /pageEvent write
# within seconds (measured 18.10), so it is refused.
NO_MASTER_COMMAND: Final = "suspendLimits"

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

# The settings, as the vendor app's ScheduleV2 and RatesV2 queries ask for
# them (no stationId on the local interface).
_SCHEDULE_FIELDS: Final = """
            scheduleV2 {
                name
                start
                end
                weekdays
                enabled
                value
                currentLimitEnabled
            }"""
_RATE_FIELDS: Final = """
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
                activeRate
            }"""
# The price in effect, which REVE_PLUS 1.0.3 reports (older firmware answers
# it with nothing): read with every measurements read, so Active Rate Cost
# turns with the station at a rate boundary rather than at the next settings
# read.
_ACTIVE_RATE_FIELD: Final = """
            ratesV2 {
                activeRate
            }"""

# What REVE_PLUS 1.0.3 adds to stationData: whether the station is in manual
# mode, and its memory and uptime (diagnostics only). Older firmware answers
# these with nothing, so they are asked for but never expected (_EXPECTED_PATHS).
_MANUAL_CONTROL: Final = "chargingManualControl"
_STATION_SYSTEM: Final[tuple[tuple[str, str], ...]] = (
    ("heapFreeSize", "heap_free"),
    ("heapMinFreeSize", "heap_min_free"),
    ("heapLargestFreeBlock", "heap_largest_free_block"),
    ("heapTotalSize", "heap_total"),
)
_UPTIME: Final = "stationUptimeMs"
_STATION_SYSTEM_FIELDS: Final = (
    _MANUAL_CONTROL, *(field for field, _key in _STATION_SYSTEM), _UPTIME
)

# Once a charger is known to be REVE, every poll reads its measurements with
# these fields: the charger app's own StationData, cut down to the fields
# reve_fields maps to /main keys, plus chargingCommand so Stop shows at once.
# /main is read only when it does not answer (MEASUREMENTS_RETRY_SECONDS keeps
# a server that times out from delaying every poll by its whole timeout).
_MEASUREMENT_FIELDS: Final = """
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
            chargingCommand""" + "".join(
    f"\n            {name}" for name in _STATION_SYSTEM_FIELDS
)


def _station_query(fields: str) -> str:
    """A ``StationData`` read of fixed fields; every document is built once,
    at import, from the constants above."""
    return f"query StationData {{\n        stationData {{{fields}\n        }}\n    }}"


MEASUREMENTS_QUERY: Final = _station_query(_MEASUREMENT_FIELDS + _ACTIVE_RATE_FIELD)
# A poll that is due for the settings asks for them in its measurements read:
# one request answered in ~90 ms against ~80 ms for the measurements alone
# (live, 2026-10-18), where the settings used to take three more.
MEASUREMENTS_AND_SETTINGS_QUERY: Final = _station_query(
    _MEASUREMENT_FIELDS + _SCHEDULE_FIELDS + _RATE_FIELDS
)
# The settings alone: read while /main serves the polls, or before a schedule
# write when nothing has been read yet. Splitting the request into one
# document per setting changed nothing about the server's HTTP 500 windows
# (2026-10-15/16), which follow its Wi-Fi reconnects.
SETTINGS_QUERY: Final = _station_query(_SCHEDULE_FIELDS + _RATE_FIELDS + """
            chargingCommand""")
# The schedules alone, re-read right before a schedule write.
SCHEDULES_QUERY: Final = _station_query(_SCHEDULE_FIELDS)

MEASUREMENTS_RETRY_SECONDS: Final = 60

# The session limits alone, to check a limit the charger refused to clear.
SESSION_LIMITS_QUERY: Final = """query StationData {
        stationData {
            session {
                energyLimit
                costLimit
                timeLimit
            }
        }
    }"""
LIMIT_CLEAR_CHECKS: Final = 5
LIMIT_CLEAR_WAIT_SECONDS: Final = 1.5

# What only /main carries on REVE, kept from the last /main read between
# GraphQL polls: the device-registry strings (for as long as the integration
# runs) and the few entity fields GraphQL has no place for (refreshed every
# five minutes, dropped after an hour without /main). A limit value GraphQL
# reports wins; /main's stands in while GraphQL reports the limit off.
MAIN_IDENTITY_FIELDS: Final[frozenset[str]] = frozenset({"manufacturer", "model", "serialNum"})
MAIN_ONLY_FIELDS: Final[frozenset[str]] = frozenset(
    {"aiModecurrent", "energyLimit", "moneyLimit", "timeLimit"}
)
MAIN_RESIDUE_REFRESH_SECONDS: Final = 300
MAIN_RESIDUE_MAX_AGE_SECONDS: Final = 3600
# The adaptive current is one of them, and it moves while an adaptive charge
# runs (7 -> 11 -> 16 -> 10 A on 2026-10-16/17). During such a session /main is
# read about once a minute for as long as its last read answered: the current
# changed 2.6 times an hour over 16 charging hours (median gap 370 s; only the
# start ramp moves within seconds), so a minute shows every change a chart can
# draw at half the requests of one read per 30 s poll. 50 s, not 60, so a poll
# a second late does not push the read to the poll after it.
ADAPTIVE_MAIN_REFRESH_SECONDS: Final = 50

# How many GraphQL reads in a row must fail before REVE API reports the
# server down: one missed reply turned it off for 30 s seven times on
# 2026-10-17, while the next read, a minute later, answered.
API_DOWN_AFTER_FAILURES: Final = 2

# Every field a measurements read asks for. The server answers a field it does
# not know with nothing (checked 2026-10-16), so a field a firmware update
# renamed would leave its entities quietly without a value: one missing from
# this many reads in a row is reported (diagnostics and a repair notice).
_EXPECTED_PATHS: Final[tuple[tuple[str, ...], ...]] = tuple(
    sorted(graphql_paths() | {("chargingCommand",)})
)
FIELDS_MISSING_READS: Final = 3

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
    # The price in effect as the station reports it (REVE_PLUS 1.0.3); None
    # when it does not.
    active_rate: float | None = None


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


def _price(value: Any) -> float | None:
    """A price the station reports, or ``None`` for anything else."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


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
    """The fields the tariff sensors read. A name or weekdays that cannot be
    read become empty / ``None``, so a format change in them cannot take the
    tariffs away; only the active rate's name or the active rate then stays
    unknown."""
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
        "name": raw["name"] if isinstance(raw.get("name"), str) else "",
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
        active_rate=_price(rates.get("activeRate")),
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
    # The third and fourth rates have no entities, only a price for Active
    # Rate Cost while one of them is in effect.
    for letter, item in zip("CD", extras.rates[2:]):
        projection[f"tarif{letter}Value"] = _hundredths(item["value"])
    return projection


def _wall_clock(main: Mapping[str, Any]) -> int | None:
    """The charger's wall clock (``systemTime``, shifted by ``timeZone``)."""
    clock = main.get("systemTime")
    if main.get("timeZone") is None or isinstance(clock, bool) or not isinstance(clock, int):
        return None
    return clock if 0 < clock < MAX_VALID_SYSTEM_TIME else None


def active_rate_index(rates: tuple[dict[str, Any], ...], clock: int) -> int | None:
    """``activeTarif`` for REVE, which keeps none: 0 the base price, 1 to 4
    the charger's rates in order (1 and 2 have rate entities of their own).

    The first enabled rate whose window holds the clock's minute, on one of its
    weekdays, applies; a window past midnight belongs to the day it started.
    ``None`` when that cannot be known: a zero-length window or unreadable
    weekdays.
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
        return index
    return 0


def station_rate_index(extras: ReveExtras, reckoned: int | None) -> int | None:
    """``activeTarif`` for the price the station says is in effect: the rate
    the windows name when it has that price, else the first enabled rate with
    it, else the base price; ``None`` when nothing has it."""
    price = _hundredths(extras.active_rate)
    matches = [
        index
        for index, item in enumerate(extras.rates, start=1)
        if item["enabled"] and _hundredths(item["value"]) == price
    ]
    if _hundredths(extras.base_rate) == price:
        matches.append(0)
    if reckoned in matches:
        return reckoned
    return matches[0] if matches else None


def _reported(station: Mapping[str, Any], path: tuple[str, ...]) -> bool:
    """Whether the reply has the field: present, or under an object the
    server reported as ``null``."""
    node: Any = station
    for name in path:
        if node is None:
            return True
        if not isinstance(node, Mapping) or name not in node:
            return False
        node = node[name]
    return True


# How far the start time computed from one read's uptime may stray from the
# last before it counts as a restart: request latency and the poll's own
# timing move it by a second or two, a restart by at least the boot time.
STARTED_TOLERANCE_SECONDS: Final = 60


def station_started(previous: float | None, now: float, uptime_ms: int) -> int:
    """When the station started, in whole epoch seconds: ``now`` less its
    uptime, keeping ``previous`` while the two agree within the tolerance."""
    started = round(now - uptime_ms / 1000)
    if previous is not None and abs(started - previous) <= STARTED_TOLERANCE_SECONDS:
        return int(previous)
    return started


def _station_system(station: Mapping[str, Any]) -> dict[str, Any]:
    """REVE_PLUS 1.0.3's memory, uptime and manual-mode flag, for diagnostics;
    what the firmware does not report is left out."""
    system: dict[str, Any] = {}
    for field, key in _STATION_SYSTEM:
        value = station.get(field)
        if type(value) is int:
            system[key] = value
    uptime = station.get(_UPTIME)
    if type(uptime) is int:
        system["uptime_seconds"] = uptime // 1000
    flag = station.get(_MANUAL_CONTROL)
    if isinstance(flag, bool):
        system["manual_control"] = flag
    return system


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
        if extras.active_rate is not None:
            active = station_rate_index(extras, active)
        if active is not None:
            payload["activeTarif"] = active
            # The name its owner gave the rate in the charger's app; the base
            # price (0) has none.
            if active and extras.rates[active - 1]["name"]:
                payload["activeTarifName"] = extras.rates[active - 1]["name"]
        if extras.charging_command is not None:
            payload[STOP_COMMAND] = int(extras.charging_command == _COMMAND_STOP)
            payload[ONE_CHARGE_COMMAND] = int(extras.charging_command == _COMMAND_CHARGE_NOW)
    unsupported = frozenset(key for key in FIELD_KEYS if key not in payload)
    return payload, unsupported


# ---------------------------------------------------------------------------
# Schedule writes
# ---------------------------------------------------------------------------


def _weekday_list(value: Any) -> list[int]:
    """Days to write: a non-empty 0-6 list (0 = Sunday), sorted, once each."""
    if not isinstance(value, list) or not value:
        raise ValueError("weekdays must be a non-empty list")  # pragma: no mutate - message text; only the exception type is ever logged
    if any(type(day) is not int or not 0 <= day <= 6 for day in value):
        raise ValueError("weekday out of range")  # pragma: no mutate - message text; only the exception type is ever logged
    return sorted(set(value))


def _coerce_slot_value(field: str, value: Any) -> Any:
    if field == "weekdays":
        return _weekday_list(value)
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
    if command in SCHEDULE_WEEKDAY_COMMANDS:
        field = "weekdays"
    elif command in SCHEDULE_COMMANDS:
        field = _SLOT_FIELDS[command[3:]]
    else:
        raise ReveCommandUnsupported(command)  # pragma: no mutate - message text; only the exception type is ever logged
    slot = int(command[2])
    if len(schedules) < slot:
        raise ReveCommandUnsupported(f"schedule {slot} does not exist")  # pragma: no mutate - message text; only the exception type is ever logged
    updated = [dict(item) for item in schedules]
    updated[slot - 1][field] = _coerce_slot_value(field, value)
    return updated


# ---------------------------------------------------------------------------
# The coordinator's GraphQL side
# ---------------------------------------------------------------------------


class ManualMode:
    """Whether the station is in manual mode.

    From the app's "ignore schedule" Start (``chargingCommand`` 0) or any Stop
    (2) until the car is unplugged, the station ignores the schedules and every
    limit, and releasing the command changes nothing. REVE_PLUS 1.0.3 reports
    the mode (``chargingManualControl``) and its word is final. Older firmware
    does not, so it is latched: on at a 0 or 2, read or written, and off when
    the car is unplugged or the session counters start over (a station restart
    under a plugged-in car). A poll without the flag carries the latch on from
    the station's last word.
    """

    def __init__(self) -> None:
        self._on = False
        self._session_time: float | None = None
        self._unplugged = False
        self._reported: bool | None = None
        self._supported = False

    @property
    def supported(self) -> bool:
        """Whether the station has ever reported the flag, and so can be told
        to leave manual mode (REVE_PLUS 1.0.3)."""
        return self._supported

    def report(self, value: Any) -> None:
        """The station's own flag from this poll's GraphQL read; anything but a
        boolean (older firmware, a ``/main`` poll) is no flag."""
        self._reported = value if isinstance(value, bool) else None
        if self._reported is not None:
            self._supported = True

    def left(self) -> None:
        """Home Assistant told the station to leave manual mode; the next read
        says whether it did."""
        self._on = False
        self._reported = None

    @property
    def on(self) -> bool:
        return self._on

    def command(self, command: int) -> None:
        """A chargingCommand just written from Home Assistant."""
        if command != _COMMAND_START:
            self._on = True
            # Shown at once; the next read says whether the station agrees.
            self._reported = None

    def restore(self, on_for: float | None = None) -> None:
        """Manual mode as it stood before Home Assistant restarted, ``on_for``
        seconds ago, unless the station already shows it ended: the connector
        is out, or the session is younger than manual mode (replugged while
        Home Assistant was down)."""
        if self._unplugged or self._reported is not None:
            return
        session = self._session_time
        if on_for is not None and session is not None and session + _RESTORE_SLACK_SECONDS < on_for:
            return
        self._on = True

    def observe(self, payload: Mapping[str, Any]) -> None:
        """One poll's normalized payload (Stop and One Charge read the command)."""
        state = payload.get("state")
        self._unplugged = (
            type(state) is int and state not in CONNECTED_STATES and state not in PLUG_UNKNOWN_STATES
        )
        if self._unplugged:
            self._on = False
            self._session_time = None
            return
        elapsed = payload.get("sessionTime")
        if type(elapsed) in (int, float):
            if self._session_time is not None and elapsed < self._session_time:
                self._on = False
            self._session_time = elapsed
        if payload.get(ONE_CHARGE_COMMAND) == 1 or payload.get(STOP_COMMAND) == 1:
            self._on = True
        if self._reported is not None:
            self._on = self._reported


# REVE's subState while it holds a charge for an outside start: after a limit
# stop, and after that limit is raised until the car takes current again.
REVE_SUBSTATE_HELD: Final = 9

# The session limits' number and on/off commands.
LIMIT_COMMANDS: Final[frozenset[str]] = frozenset(
    key for limit in _LIMIT_WRITES for key in limit[:2]
)


def limit_refusal(
    command: str, state: int | None, manual_mode: ManualMode, *, limit_reached: bool = False
) -> str | None:
    """Why a REVE session limit cannot be set, changed or switched now;
    ``None`` when it can, or for any other command.

    REVE ignores a limit in manual mode and drops it when the connector is
    pulled out, so a limit is touched only outside manual mode while the car
    is charging or a limit has stopped the charge (Anton's rule, 2026-10-18).
    A limit stop is no manual mode: raising the limit resumes the charge.
    ``limit_reached`` also covers the 5/9 hold after such a raise, until the
    car takes current again.
    """
    if command not in LIMIT_COMMANDS:
        return None
    if manual_mode.on:
        return "The charger is in REVE manual mode: limits can be changed after the connector is replugged"  # pragma: no mutate - message text
    if state != DEVICE_STATE_CHARGING and not limit_reached:
        return "REVE limits can be changed only while the car is charging or a limit has stopped it"  # pragma: no mutate - message text
    return None


def snapshot_limit_refusal(command: str, snapshot: Any, manual_mode: ManualMode) -> str | None:
    """``limit_refusal`` for the charger as the last poll left it."""
    return limit_refusal(
        command,
        snapshot.get_int("state"),
        manual_mode,
        limit_reached=reached_limit(snapshot.get) is not None
        or (snapshot.get_int("state") == DEVICE_STATE_COMPLETE and snapshot.get_int("subState") == REVE_SUBSTATE_HELD),
    )


class ReveApi:
    """Reads and writes the settings REVE keeps only in GraphQL."""

    def __init__(self, updater: Any) -> None:
        self._updater = updater
        self._extras: ReveExtras | None = None  # pragma: no mutate - read only behind `_fetched_at`, which is None here
        self._fetched_at: float | None = None
        self._retry_at = 0.0  # pragma: no mutate - any start below the monotonic clock is equivalent
        self._failures = 0
        self._last_error: str | None = None  # pragma: no mutate - only read once a read has set or cleared it
        # GraphQL reads failed in a row, of any document, and settings reads
        # failed in a row. The second is kept apart because every good
        # measurements read clears the first: with only the settings read
        # failing, REVE API stays off until a settings read succeeds instead of
        # flipping between retries.
        self._failure_streak = 0
        self._settings_streak = 0
        # Whether any GraphQL read has succeeded: the tolerance for a missed
        # reply needs a server seen answering first.
        self._answered = False
        # Reads in a row each expected field was missing from.
        self._missing_streaks: dict[str, int] = {}
        self._measurements_retry_at = 0.0  # pragma: no mutate - any start below the monotonic clock is equivalent
        self._measurement_reads = 0
        self._measurement_failures = 0
        self._main_fallbacks = 0
        self._last_source: str | None = None
        self._residue: dict[str, Any] = {}
        self._residue_at: float | None = None
        self._residue_tried_at: float | None = None
        self._residue_answered = False
        self._session: tuple[dict[str, Any], float] | None = None
        self._session_unplugged = False
        # A limit's value in /main read units, kept while the limit is off
        # (REVE then reports null) so its number shows what switching it back
        # on would apply.
        self._limit_values: dict[str, int | float] = {}
        self.manual_mode = ManualMode()
        self._station_system: dict[str, Any] = {}
        self._started_at: int | None = None

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
        if self._settings_due(time.monotonic()):
            await self.refresh()

    def _settings_due(self, now: float) -> bool:
        if now < self._retry_at:
            return False
        return self._fetched_at is None or now - self._fetched_at >= EXTRAS_REFRESH_SECONDS

    async def refresh(self) -> None:
        """Re-read schedules and rates now; a failure only schedules the retry."""
        try:
            self._store(await self._read_extras())
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            self._settings_failed(err)

    def _read_failed(self, err: BaseException) -> None:
        self._failure_streak += 1
        self._last_error = type(err).__name__

    def _settings_failed(self, err: BaseException) -> None:
        """A read that asked for the settings failed: retry them alone, after
        the back-off. Until one succeeds, the measurements are read without
        them, so a server that refuses only the settings keeps its readings."""
        self._read_failed(err)
        self._failures += 1
        self._settings_streak += 1
        self._retry_at = time.monotonic() + EXTRAS_RETRY_SECONDS
        _LOGGER.debug("REVE schedules/rates read failed: %s", type(err).__name__)

    # -- measurements --------------------------------------------------------

    def measurements_due(self) -> bool:
        """Whether this poll should try GraphQL (not while backing off)."""
        return time.monotonic() >= self._measurements_retry_at

    async def read_measurements(
        self, validate: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """This poll's measurements in ``/main`` keys. Raises when GraphQL gives
        no usable reply, or one ``validate`` (the ``/main`` rules) rejects.
        Asks for the settings too when they are due and the last read of them
        did not fail."""
        with_settings = self._settings_streak == 0 and self._settings_due(time.monotonic())
        try:
            station = await self._station(
                MEASUREMENTS_AND_SETTINGS_QUERY if with_settings else MEASUREMENTS_QUERY
            )
            main = station_to_main(station)
            if "state" not in main:
                raise ValueError("no stationState")  # pragma: no mutate - message text; only the exception type is ever logged
            if validate is not None:
                main = validate(main)
            extras = parse_extras({"stationData": station}) if with_settings else None
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            self._measurement_failures += 1
            self._measurements_retry_at = time.monotonic() + MEASUREMENTS_RETRY_SECONDS
            _LOGGER.debug("REVE measurements read failed: %s", type(err).__name__)
            if with_settings:
                self._settings_failed(err)
            else:
                self._read_failed(err)
            raise
        self._measurement_reads += 1
        self._failure_streak = 0
        self._answered = True
        self._check_fields(station)
        self._last_error = None
        self._last_source = "graphql"
        if extras is not None:
            self._store(extras)
        self._session = (
            {key: main[key] for key in SESSION_FIELDS if key in main},
            time.monotonic(),
        )
        self._session_unplugged = main.get("state") == DEVICE_STATE_STANDBY
        self._note_charging_command(station.get("chargingCommand"))
        rates = station.get("ratesV2")
        self._note_active_rate(_price(rates.get("activeRate")) if isinstance(rates, dict) else None)
        self.manual_mode.report(station.get(_MANUAL_CONTROL))
        self._station_system = _station_system(station)
        uptime = station.get(_UPTIME)
        if type(uptime) is int:
            self._started_at = station_started(self._started_at, time.time(), uptime)
        return main

    def _check_fields(self, station: Mapping[str, Any]) -> None:
        self._missing_streaks = {
            name: self._missing_streaks.get(name, 0) + 1
            for path in _EXPECTED_PATHS
            if not _reported(station, path)
            for name in (".".join(path),)
        }

    @property
    def missing_fields(self) -> tuple[str, ...]:
        """Fields every one of the last ``FIELDS_MISSING_READS`` measurements
        reads lacked; a field is dropped from the list by the first read that
        has it."""
        return tuple(
            name
            for name, reads in self._missing_streaks.items()
            if reads >= FIELDS_MISSING_READS
        )

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

    def main_residue_due(self, measurements: Mapping[str, Any] | None = None) -> bool:
        """Whether a GraphQL poll should also read ``/main`` for its own fields:
        every five minutes, whether or not the last try found ``/main``, and
        once a minute in an adaptive session while ``/main`` answers."""
        tried = self._residue_tried_at
        if tried is None:
            return True
        elapsed = time.monotonic() - tried
        if elapsed >= MAIN_RESIDUE_REFRESH_SECONDS:
            return True
        return (
            elapsed >= ADAPTIVE_MAIN_REFRESH_SECONDS
            and self._residue_answered
            and measurements is not None
            and measurements.get("state") in SESSION_ACTIVE_STATES
            and measurements.get("aiStatus") not in (None, 0)
        )

    def main_residue_tried(self) -> None:
        self._residue_tried_at = time.monotonic()
        self._residue_answered = False

    def keep_main_residue(self, main: Mapping[str, Any]) -> None:
        """Keep what only ``/main`` carries from a ``/main`` reply."""
        self._residue = {
            key: main[key]
            for key in MAIN_IDENTITY_FIELDS | MAIN_ONLY_FIELDS
            if key in main
        }
        self._residue_at = self._residue_tried_at = time.monotonic()
        self._residue_answered = True

    def from_main(self, main: Mapping[str, Any], *, fallback: bool) -> dict[str, Any]:
        """A REVE ``/main`` payload as a poll shows it: the frozen session
        replaced by the last GraphQL one while that is recent, else left out."""
        self.keep_main_residue(main)
        self._last_source = "main"
        self.manual_mode.report(None)
        # A /main poll says nothing of the memory or the price now; the start
        # time stands, and the rate windows decide the price.
        self._station_system = {}
        self._note_active_rate(None)
        if fallback:
            self._main_fallbacks += 1
        payload = {key: value for key, value in main.items() if key not in SESSION_FIELDS}
        state = get_safe_value(main, "state", int)
        if state == DEVICE_STATE_STANDBY:
            self._session_unplugged = True
        elif state in CONNECTED_STATES and self._session_unplugged:
            # The cached tail belongs to the unplugged session. Reusing it
            # after a replug can falsely satisfy the new session's SOC limit.
            self._session = None
        held = self._session
        if held is not None and time.monotonic() - held[1] <= SESSION_HOLD_SECONDS:
            payload.update(held[0])
        return payload

    def _residue_fresh(self, now: float) -> bool:
        return self._residue_at is not None and now - self._residue_at <= MAIN_RESIDUE_MAX_AGE_SECONDS

    def _note_active_rate(self, price: float | None) -> None:
        extras = self._extras
        if extras is not None and extras.active_rate != price:
            self._extras = replace(extras, active_rate=price)

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
        if command in SCHEDULE_COMMANDS or command in SCHEDULE_WEEKDAY_COMMANDS:
            return partial(self.write_schedule, command)
        if command in SCHEDULE_ENERGY_COMMANDS or command == NO_MASTER_COMMAND:
            return partial(self._refuse, command)  # pragma: no mutate - the command only names the refusal
        if command == ONE_CHARGE_COMMAND:
            return self.set_one_charge
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
        if command in _RESET_WRITES:
            return partial(self.reset_counter, _RESET_WRITES[command])
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

    async def reset_counter(self, field: str, _value: Any) -> None:
        await self._mutate({"iem": {field: 1}})

    def _require_limit_window(self, command: str) -> None:
        reason = snapshot_limit_refusal(command, self._updater.snapshot, self.manual_mode)
        if reason is not None:
            raise ReveCommandUnsupported(reason)

    async def set_limit(self, key: str, flag: str, field: str, factor: int, value: Any) -> None:
        """A limit's value (write units). REVE has no on/off flag: writing a
        value switches the limit on, so while it is off the value is only kept
        for when it is switched on."""
        self._require_limit_window(key)
        written = _whole(value)
        if self._updater.snapshot.get_int(flag) == 1:
            await self._mutate({"session": {field: written}})
        self._limit_values[key] = written / factor if factor != 1 else written

    async def switch_limit(self, key: str, flag: str, field: str, factor: int, value: Any) -> None:
        """Switch a limit on with its kept (or current) value, or off: ``null``."""
        self._require_limit_window(flag)
        if not _bool(value):
            # The value stays shown: every GraphQL read of a limit that is on
            # keeps it (``with_main_residue``), and /main carries it meanwhile.
            await self._clear_limit(field)
            return
        read = self._limit_values.get(key)
        if read is None:
            read = _limit_reading(self._updater.snapshot.get(key))
        if read is None or read <= 0:
            raise ReveCommandUnsupported(f"{key} has no value to switch on with")  # pragma: no mutate - message text; only the exception type is ever logged
        await self._mutate({"session": {field: round(read * factor)}})

    async def _clear_limit(self, field: str) -> None:
        """Clear a session limit with ``null``.

        REVE answers that write with ``success: false`` and drops the limit
        all the same (seen for all three limits on 2.4.0), so a refusal is
        settled by what the charger reports a moment later.
        """
        try:
            await self._mutate({"session": {field: None}})
            return
        except GraphQLError:
            pass
        for _ in range(LIMIT_CLEAR_CHECKS):
            await asyncio.sleep(LIMIT_CLEAR_WAIT_SECONDS)
            session = (await self._station(SESSION_LIMITS_QUERY)).get("session")
            if isinstance(session, dict) and field in session and session[field] is None:
                return
        raise GraphQLError("REVE did not clear the limit")  # pragma: no mutate - message text; only the exception type is ever logged

    async def set_station_current(self, value: Any) -> None:
        await self._mutate({"stationCurrent": _whole(value)})

    async def set_stop(self, value: Any) -> None:
        """Stop (1) or release (0) charging the way the vendor app does; a
        release also leaves manual mode where the station can be told to."""
        stop = _bool(value)
        await self._write_charging_command(_COMMAND_STOP if stop else _COMMAND_START)
        if not stop:
            await self._leave_manual_mode()

    async def set_one_charge(self, value: Any) -> None:
        """Charge now, ignoring the schedules, the way the vendor app's Start
        does (0); off releases it (1). Stop shares the command, so off leaves
        a stopped charge stopped and writes nothing unless it is known to be 0."""
        if _bool(value):
            await self._write_charging_command(_COMMAND_CHARGE_NOW)
            return
        extras = self._extras
        if extras is not None and extras.charging_command == _COMMAND_CHARGE_NOW:
            await self._write_charging_command(_COMMAND_START)
        extras = self._extras
        if extras is not None and extras.charging_command == _COMMAND_START:
            await self._leave_manual_mode()

    async def _leave_manual_mode(self) -> None:
        """With Stop and One Charge both off, back to the schedules and limits
        without unplugging, as on Eveus firmware (Anton, 2026-10-23). Only
        REVE_PLUS 1.0.3 can be told to; older REVE stays in manual mode until
        the car is unplugged. Never done on a read alone: the app's Pause then
        Resume also leaves both off, and its user expects the charge to go on."""
        if not (self.manual_mode.supported and self.manual_mode.on):
            return
        await self._mutate({_MANUAL_CONTROL: False})
        self.manual_mode.left()

    async def _write_charging_command(self, command: int) -> None:
        await self._mutate({"chargingCommand": command})
        self.manual_mode.command(command)
        extras = self._extras
        if extras is not None:
            self._store(replace(extras, charging_command=command))

    async def write_schedule(self, command: str, value: Any) -> None:
        # Read right before writing: the list is replaced whole, and the app
        # may have changed another schedule since the last poll. The rates and
        # Stop stand on the last read; with none yet, read everything.
        held = self._extras
        if held is None:
            current = await self._read_extras()
        else:
            schedules = parse_schedules(await self._station(SCHEDULES_QUERY))
            current = replace(held, schedules=schedules)
        updated = apply_schedule_command(list(current.schedules), command, value)
        await self._mutate({"scheduleV2": updated})
        self._store(replace(current, schedules=tuple(updated)))

    async def _refuse(self, command: str, _value: Any) -> None:
        raise ReveCommandUnsupported(command)  # pragma: no mutate - message text; only the exception type is ever logged

    @property
    def answering(self) -> bool | None:
        """Whether GraphQL answers: off once ``API_DOWN_AFTER_FAILURES`` reads
        in a row failed, or as many settings reads, and after any failure
        before the first good read; ``None`` before the first read."""
        if max(self._failure_streak, self._settings_streak) >= API_DOWN_AFTER_FAILURES:
            return False
        if self._answered:
            return True
        return False if self._failure_streak else None

    @property
    def station_system(self) -> dict[str, Any]:
        """The last GraphQL read's memory figures (bytes) and the station's
        start time (``started_at``, epoch seconds), as far as reported."""
        system = dict(self._station_system)
        if self._started_at is not None:
            system["started_at"] = self._started_at
        return system

    def diagnostics(self) -> dict[str, Any]:
        extras = self.extras
        return {
            "graphql_ok": extras is not None and self.answering is True,
            "schedules": None if extras is None else len(extras.schedules),
            "rates": None if extras is None else len(extras.rates),
            "read_age_seconds": (
                None
                if self._fetched_at is None
                else round(time.monotonic() - self._fetched_at)
            ),
            "read_failures": self._failures,
            "last_error": self._last_error,
            "missing_fields": list(self.missing_fields),
            "station": self._station_system,
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
        self._failure_streak = 0
        self._settings_streak = 0
        self._answered = True

    async def _read_extras(self) -> ReveExtras:
        return parse_extras({"stationData": await self._station(SETTINGS_QUERY)})

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
