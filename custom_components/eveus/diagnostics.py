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
    payload: dict[str, Any] = {
        "entry": {
            "title": "Eveus Charger",
            "data": async_redact_data(dict(entry.data), _sensitive_keys(dict(entry.data))),
            "device_number": (
                runtime_data.device_number if runtime_data is not None else None
            ),
        },
    }

    if runtime_data is None:
        payload["setup"] = {
            "ready": False,
            "note": "Integration setup did not complete; runtime data unavailable.",
        }
        return payload

    updater = runtime_data.updater
    soc_calculator = getattr(runtime_data, "soc_calculator", None)
    data = updater.data or {}
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
                "firmware": data.get("verFWMain")
                or getattr(updater, "_init_fw_fallback", None),
                "wifi_firmware": data.get("verFWWifi"),
                "state": data.get("state"),
                # Original firmware-1.x state code when the coordinator
                # translated it to the modern domain; None on modern firmware.
                "legacy_raw_state": data.get(LEGACY_RAW_STATE_KEY),
                "substate": data.get("subState"),
                "current_set": data.get("currentSet"),
                "model": data.get("model"),
                "manufacturer": data.get("manufacturer"),
            },
            # SOC inputs and the external-sensor seeding outcome. Absent when
            # setup predates the calculator (older entries under test).
            **(
                {"soc": _soc_diagnostics(hass, entry, soc_calculator)}
                if soc_calculator is not None
                else {}
            ),
            # Full /main payload with sensitive identifiers removed. Useful for
            # bug reports — gives the developer the exact field set the device
            # reported without leaking serials or LAN addresses. Unknown but
            # identifying-looking firmware fields are redacted too.
            # Synthetic coordinator keys are stripped so raw_main stays the
            # exact field set the device reported (the legacy raw state is
            # surfaced under "device" above instead).
            "raw_main": async_redact_data(
                {k: v for k, v in data.items() if k != LEGACY_RAW_STATE_KEY},
                _sensitive_keys(data),
            ),
        }
    )
    return payload
