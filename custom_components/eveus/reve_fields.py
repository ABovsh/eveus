"""REVE: which ``/main`` field each GraphQL ``stationData`` field stands for.

REVE reports nearly all of ``/main`` through its local GraphQL ``stationData``
as well, under other names and partly in other units. This table is the one
place that says which is which, so a GraphQL reply can be turned into the very
``/main`` keys the entities already read.

Every GraphQL field named here comes from the ``StationData`` document of the
web app REVE serves itself (at ``/``; it polls that document every 4 s). The
REVE server silently drops a field it does not know, so a field outside that
document would only ever read as missing; none is sent. Checked against live captures of
``/main`` and ``stationData`` from a REVE Plus (REVE_PLUS_1.0.0 / 2.4.0) on
2026-10-15: every field the two shared carried the same value, except the
session ones ``/main`` reports frozen.

Unit and shape differences:

* flags are booleans in GraphQL and 0/1 in ``/main``;
* ``timeSettings.systemTime`` is an ISO-8601 UTC string; ``/main`` counts
  seconds of local time (UTC plus ``timeZone`` hours);
* session limits are ``null`` when off, which ``/main`` reports as the ``*S``
  flag; ``energyLimit`` is in watt-hours, ``/main`` in kilowatt-hours.

The schedules, tariffs and Stop come from their own documents (``reve.py``).
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _flag(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and value in (0, 1):
        return value
    return None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


# /main key -> where GraphQL stationData reports the same value.
_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "state": ("stationState",),
    "subState": ("stationSubState",),
    "pilot": ("pilotStatus",),
    "sessionStarted": ("isSessionStarted",),
    "currentSet": ("stationCurrent",),
    "curDesign": ("designedCurrent",),
    "minCurrent": ("minCurrent",),
    "minVoltage": ("minVoltage",),
    "gridRange": ("gridRange",),
    "RSSI": ("RSSI",),
    "evseType": ("chargerType",),
    "typeRelay": ("releType",),
    "aiStatus": ("adaptiveModeStatus",),
    "aiVoltage": ("voltageThreshold",),
    "ground": ("ground",),
    "groundCtrl": ("isGroundConnected",),
    "timeZone": ("timeSettings", "timeZone"),
    "evseEnabled": ("isChargerEnabled",),
    "suspendErrors": ("isSuspendError",),
    "oneCharge": ("isOneChargeEnabled",),
    "totalEnergy": ("totalEnergy",),
    "sessionTime": ("session", "sessionTime"),
    "sessionEnergy": ("session", "sessionEnergy"),
    "sessionMoney": ("session", "sessionCost"),
    "ocppEnabled": ("ocpp", "ocppEnabled"),
    "stationId": ("ocpp", "stationId"),
    "IEM1": ("iem", "IEM1"),
    "IEM2": ("iem", "IEM2"),
    "IEM1_money": ("iem", "IEM1_money"),
    "IEM2_money": ("iem", "IEM2_money"),
    "vBat": ("measurements", "batteryVoltage"),
    "curMeas1": ("measurements", "curMeasurement1"),
    "curMeas2": ("measurements", "curMeasurement2"),
    "curMeas3": ("measurements", "curMeasurement3"),
    "voltMeas1": ("measurements", "voltMeasurement1"),
    "voltMeas2": ("measurements", "voltMeasurement2"),
    "voltMeas3": ("measurements", "voltMeasurement3"),
    "powerMeas": ("measurements", "powerMeasurement"),
    "temperature1": ("measurements", "temperature", "temperature1"),
    "temperature2": ("measurements", "temperature", "temperature2"),
    "lang": ("language",),
    "STA_IP_Addres": ("STA_IP_Addres",),
    "verFWMain": ("mainFWVersion",),
    "verFWWifi": ("wifiFWVersion",),
}
# Booleans in GraphQL, 0/1 in /main; and the text fields. The rest are numbers.
_FLAG_KEYS: Final[frozenset[str]] = frozenset(
    {"sessionStarted", "groundCtrl", "evseEnabled", "suspendErrors", "oneCharge", "ocppEnabled"}
)
_TEXT_KEYS: Final[frozenset[str]] = frozenset(
    {"stationId", "STA_IP_Addres", "verFWMain", "verFWWifi"}
)

_SYSTEM_TIME: Final = ("timeSettings", "systemTime")

# /main limit key, its on/off flag, the GraphQL session field, and the factor
# from GraphQL units to /main units.
_LIMITS: Final[tuple[tuple[str, str, str, int | float], ...]] = (
    ("energyLimit", "energyLimitS", "energyLimit", 0.001),
    ("moneyLimit", "moneyLimitS", "costLimit", 1),
    ("timeLimit", "timeLimitS", "timeLimit", 1),
)

# Every /main key station_to_main can produce.
MAIN_KEYS: Final[frozenset[str]] = frozenset(
    {*_FIELDS, "systemTime", *(key for limit in _LIMITS for key in limit[:2])}
)

# Entity fields with no GraphQL source at all. Leakage is reported only as raw
# ADC counts (``/main`` and ``serviceData`` alike); the charger has no
# "disable limits" master or per-schedule energy cap any more; adaptive-mode
# current and the OCPP connection flag have no field the app reads; the
# classic ``aiMode`` write key is ``adaptiveModeStatus`` there.
NO_GRAPHQL_SOURCE: Final[frozenset[str]] = frozenset(
    {
        "leakValue", "leakValueH",
        "suspendLimits",
        "sh1EnergyEnable", "sh1EnergyValue", "sh2EnergyEnable", "sh2EnergyValue",
        "aiMode", "aiModecurrent",
        "ocppconnected",
    }
)


_MISSING: Final = object()


def _lookup(station: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = station
    for name in path:
        if not isinstance(node, Mapping) or name not in node:
            return _MISSING
        node = node[name]
    return node


def _local_seconds(iso: Any, zone: Any) -> int | None:
    """``/main``'s ``systemTime``: the UTC instant plus ``timeZone`` hours."""
    if not isinstance(iso, str) or _number(zone) is None:
        return None
    try:
        instant = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if instant.tzinfo is None:
        return None
    return int(instant.timestamp()) + int(zone * 3600)


def _convert(key: str, raw: Any) -> Any:
    if key in _FLAG_KEYS:
        return _flag(raw)
    if key in _TEXT_KEYS:
        return _text(raw)
    return _number(raw)


def station_to_main(station: Mapping[str, Any]) -> dict[str, Any]:
    """The ``/main`` keys a GraphQL ``stationData`` reply reports.

    A field the reply leaves out, or reports as ``null`` or in an unusable
    shape, is left out rather than reported as ``None``. The input is never
    mutated.
    """
    main: dict[str, Any] = {}
    for key, path in _FIELDS.items():
        raw = _lookup(station, path)
        if raw is _MISSING:
            continue
        value = _convert(key, raw)
        if value is not None:
            main[key] = value
    clock = _local_seconds(_lookup(station, _SYSTEM_TIME), main.get("timeZone"))
    if clock is not None:
        main["systemTime"] = clock
    session = _lookup(station, ("session",))
    if isinstance(session, Mapping):
        for key, flag, field, factor in _LIMITS:
            if field not in session:
                continue
            limit = session[field]
            if limit is None:
                main[flag] = 0
                continue
            value = _number(limit)
            if value is not None:
                main[key] = value * factor
                main[flag] = 1
    return main


def graphql_paths() -> frozenset[tuple[str, ...]]:
    """Every GraphQL field the map reads, as a path from ``stationData``."""
    paths = set(_FIELDS.values())
    paths.add(_SYSTEM_TIME)
    paths.update(("session", field) for _key, _flag_key, field, _factor in _LIMITS)
    return frozenset(paths)
