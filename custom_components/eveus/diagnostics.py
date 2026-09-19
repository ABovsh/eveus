"""Diagnostics support for Eveus."""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import EveusConfigEntry
from .const import CONF_EXTERNAL_SOC_ENTITY, LEGACY_RAW_STATE_KEY

# Redacted on every diagnostics download — credentials, host, IDs, and any
# /main field that exposes the LAN address or hardware serial.
# NOTE: every entry here except host, stationId, unique_id and username is also
# matched by _SENSITIVE_NAME_RE below, so mutating those out of this set cannot
# change the output -- the heuristic still catches them. The four that are not
# heuristic-matched are pinned individually in tests/test_diagnostics.py.
TO_REDACT = {
    "password",
    "username",
    "host",
    "unique_id",
    # /main fields with identifying device data
    "serialNum",
    "serialNumCPU",
    "stationId",
    "STA_IP_Addres",
    "fwCRC32",
}

# Defense in depth: also redact any field whose name *looks* identifying, so a
# future firmware key (a new SSID/MAC/IP/serial/token field) cannot leak into a
# shared diagnostics download just because it was not on the explicit list.
# Telemetry field names (powerMeas, sessionEnergy, tarif*, IEM1_money, …) do not
# match these substrings.
_SENSITIVE_NAME_RE = re.compile(
    r"ssid|passw|secret|token|serial|imei|uuid|mac|addr|ipaddr|"
    r"ip_addr|latitude|longitude|geoloc|crc|auth|credential|key|pwd|pin",
    re.IGNORECASE,
)


def _collect_sensitive_keys(value: Any, acc: set[str]) -> None:
    """Walk nested dicts/lists, adding any key whose name looks identifying."""
    if isinstance(value, Mapping):
        for key, sub in value.items():
            if _SENSITIVE_NAME_RE.search(str(key)):
                acc.add(key)
            _collect_sensitive_keys(sub, acc)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect_sensitive_keys(item, acc)


def _sensitive_keys(data: Mapping[str, Any]) -> set[str]:
    """Return the explicit + name-heuristic set of keys to redact for `data`.

    The heuristic walks nested structures, so a sensitive key introduced by a
    future firmware under a nested object is still redacted, not just top-level.
    """
    keys = set(TO_REDACT)
    _collect_sensitive_keys(data, keys)
    return keys


# Allowlists (I18). raw_main and entry data report values only for fields the
# integration knows; an unknown field may carry anything a future firmware or
# config migration adds, so it is summarised as name -> type, and its name is
# echoed only when it cannot itself identify anyone. The redaction above stays
# as defense in depth for known fields (serialNum, host, ...).
# /main field names seen on modern firmware and on firmware 1.x (issue #11).
_KNOWN_MAIN_FIELDS = frozenset({
    "activeTarif", "adapter", "add_curr", "aiAutoPercent", "aiModecurrent",
    "aiPatameter", "aiPowerDrop", "aiStatus", "aiVoltage", "aiVoltageDrop",
    "aiVoltageStart", "broadcastMode", "curDesign", "curMeas1", "curMeas2",
    "curMeas3", "current_tarif", "currentSchedule1", "currentSchedule2",
    "currentSet", "delayedLimit", "displayOrientation", "energyLimit",
    "energyLimitS", "energySchedule1", "energySchedule2", "evseEnabled", "evseType",
    "fixedMode", "fwCRC32", "gridRange", "ground", "groundCtrl", "IEM1",
    "IEM1_money", "IEM2", "IEM2_money", "lang", "leakValue", "leakValueH",
    "led_ctrl", "limitsStatus", "logReady", "manufacturer", "minCurrent",
    "minVoltage", "model", "moneyLimit", "moneyLimitS", "ocppconnected",
    "ocppEnabled", "ocppOfflineAva", "ocppVendor", "one_charge", "oneCharge",
    "pilot", "powerMeas", "restricted_mode", "RSSI", "scanComplete", "serialNum",
    "serialNumCPU", "sessionEnergy", "sessionMoney", "sessionStart",
    "sessionStarted", "sessionTime", "sh1CurrentEnable", "sh1CurrentValue",
    "sh1Enabled", "sh1EnergyEnable", "sh1EnergyValue", "sh1Start", "sh1Stop",
    "sh2CurrentEnable", "sh2CurrentValue", "sh2Enabled", "sh2EnergyEnable",
    "sh2EnergyValue", "sh2Start", "sh2Stop", "SNflag", "STA_IP_Addres",
    "startSchedule1", "startSchedule2", "state", "stationId", "stopSchedule1",
    "stopSchedule2", "subState", "suspendErrors", "suspendLimits",
    "suspendSchedules", "switchState", "systemTime", "tarif", "tarif_2",
    "tarif_2_start", "tarif_2_status", "tarif_2_stop", "tarif_3", "tarif_3_start",
    "tarif_3_status", "tarif_3_stop", "tarifAEnable", "tarifAStart", "tarifAStop",
    "tarifAValue", "tarifBEnable", "tarifBStart", "tarifBStop", "tarifBValue",
    "temperature1", "temperature2", "timeLimit", "timeLimitS", "timeMsg",
    "timerType", "timeZone", "tmp_ctrl", "tmp_ctrl_val", "totalEnergy", "typeEvse",
    "typeRelay", "vBat", "verFWMain", "verFWStatus", "verFWWifi", "voltMeas1",
    "voltMeas2", "voltMeas3",
})
_KNOWN_ENTRY_FIELDS = frozenset({
    "host", "username", "password", "unique_id", "scheme", "model", "phases",
    "device_number", "soc_mode", "initial_soc", "target_soc", "battery_capacity",
    "soc_correction", CONF_EXTERNAL_SOC_ENTITY,
})
_SCALARS = (str, int, float, bool, type(None))
_SAFE_FIELD_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,31}")
_DIGIT_RUN_RE = re.compile(r"\d{3}")


def _scalar(value: Any) -> Any:
    """Return a scalar verbatim; anything nested collapses to its type name."""
    return value if isinstance(value, _SCALARS) else f"<{type(value).__name__}>"


def _split_known(
    data: Mapping[str, Any], known: frozenset[str]
) -> tuple[dict[str, Any], dict[str, str], int]:
    """Split `data` into redacted known scalars, echoable unknown names, suppressed count."""
    known_values = {k: _scalar(v) for k, v in data.items() if k in known}
    unknown: dict[str, str] = {}
    suppressed = 0
    for key, value in data.items():
        if key in known:
            continue
        name = str(key)
        if (
            _SAFE_FIELD_NAME_RE.fullmatch(name)
            and not _DIGIT_RUN_RE.search(name)
            and not _SENSITIVE_NAME_RE.search(name)
        ):
            unknown[name] = type(value).__name__
        else:
            suppressed += 1
    return async_redact_data(known_values, _sensitive_keys(known_values)), unknown, suppressed


def _soc_diagnostics(
    hass: HomeAssistant | None,
    entry: EveusConfigEntry,
    calculator: Any,
) -> dict[str, Any]:
    """SOC inputs plus why the last external-sensor seed did or did not happen.

    None of it is identifying — percentages, a kWh capacity and an entity id —
    so it is reported verbatim. Without it a SOC bug report cannot be acted on:
    every SOC figure is derived from these four values.
    """
    external = entry.data.get(CONF_EXTERNAL_SOC_ENTITY) or None
    state = None
    if external and hass is not None:
        reading = hass.states.get(external)
        state = None if reading is None else reading.state
    last_seed = getattr(calculator, "last_seed", None) or {}
    return {
        "initial_soc": calculator.initial_soc,
        "target_soc": calculator.target_soc,
        "battery_capacity": calculator.battery_capacity,
        "soc_correction": calculator.soc_correction,
        "helpers_available": calculator.are_helpers_available(),
        "external_soc_entity": external,
        "external_soc_state": state,
        "seeded_this_cycle": bool(last_seed.get("seeded")),
        "seed_detail": last_seed.get("detail"),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: EveusConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime_data = getattr(entry, "runtime_data", None)
    entry_data, entry_unknown, entry_suppressed = _split_known(
        entry.data, _KNOWN_ENTRY_FIELDS
    )
    payload: dict[str, Any] = {
        "entry": {
            "title": "Eveus Charger",  # pragma: no mutate - display-only text, no behaviour attached
            "data": entry_data,
            "unknown_fields": entry_unknown,
            "unknown_fields_suppressed": entry_suppressed,
            "device_number": (
                runtime_data.device_number if runtime_data is not None else None
            ),
        },
    }

    if runtime_data is None:
        payload["setup"] = {
            "ready": False,
            "note": "Integration setup did not complete; runtime data unavailable.",  # pragma: no mutate - display-only text, no behaviour attached
        }
        return payload

    updater = runtime_data.updater
    soc_calculator = getattr(runtime_data, "soc_calculator", None)
    data = updater.data or {}
    main_known, main_unknown, main_suppressed = _split_known(
        {k: v for k, v in data.items() if k != LEGACY_RAW_STATE_KEY},
        _KNOWN_MAIN_FIELDS,
    )
    quality = updater.connection_quality
    payload.update(
        {
            "coordinator": {
                "last_update_success": updater.last_update_success,
                "update_interval": (
                    updater.update_interval.total_seconds()
                    if updater.update_interval is not None
                    else None
                ),
                "connection_quality": quality,
                "is_likely_offline": updater.is_likely_offline,
                "consecutive_failures": quality.get("consecutive_failures"),
                "last_error": quality.get("last_error"),
            },
            "device": {
                # Firmware 1.x omits verFWMain from /main; the version is then
                # resolved once from /init and kept on the updater (issue #11).
                "firmware": _scalar(
                    data.get("verFWMain")
                    or getattr(updater, "_init_fw_fallback", None)
                ),
                "wifi_firmware": _scalar(data.get("verFWWifi")),
                "state": _scalar(data.get("state")),
                # Original firmware-1.x state code when the coordinator
                # translated it to the modern domain; None on modern firmware.
                "legacy_raw_state": _scalar(data.get(LEGACY_RAW_STATE_KEY)),
                "substate": _scalar(data.get("subState")),
                "current_set": _scalar(data.get("currentSet")),
                "model": _scalar(data.get("model")),
                "manufacturer": _scalar(data.get("manufacturer")),
            },
            # SOC inputs and the external-sensor seeding outcome. Absent when
            # setup predates the calculator (older entries under test).
            **(
                {"soc": _soc_diagnostics(hass, entry, soc_calculator)}
                if soc_calculator is not None
                else {}
            ),
            # /main fields the integration knows, with identifying ones redacted:
            # the exact field set the device reported, without serials or LAN
            # addresses. Synthetic coordinator keys are stripped (the legacy raw
            # state is surfaced under "device" above). Unknown fields: type only.
            "raw_main": main_known,
            "unknown_main_fields": main_unknown,
            "unknown_main_fields_suppressed": main_suppressed,
        }
    )
    return payload
