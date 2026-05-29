"""Diagnostics support for Eveus."""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import EveusConfigEntry

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
    r"ip_addr|latitude|longitude|geoloc|crc",
    re.IGNORECASE,
)


def _sensitive_keys(data: Mapping[str, Any]) -> set[str]:
    """Return the explicit + name-heuristic set of keys to redact for `data`."""
    keys = set(TO_REDACT)
    keys.update(key for key in data if _SENSITIVE_NAME_RE.search(str(key)))
    return keys


async def async_get_config_entry_diagnostics(
    _hass: HomeAssistant,
    entry: EveusConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime_data = getattr(entry, "runtime_data", None)
    payload: dict[str, Any] = {
        "entry": {
            "title": "Eveus Charger",
            "data": async_redact_data(dict(entry.data), TO_REDACT),
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
                "firmware": data.get("verFWMain"),
                "wifi_firmware": data.get("verFWWifi"),
                "state": data.get("state"),
                "substate": data.get("subState"),
                "current_set": data.get("currentSet"),
                "model": data.get("model"),
                "manufacturer": data.get("manufacturer"),
            },
            # Full /main payload with sensitive identifiers removed. Useful for
            # bug reports — gives the developer the exact field set the device
            # reported without leaking serials or LAN addresses. Unknown but
            # identifying-looking firmware fields are redacted too.
            "raw_main": async_redact_data(dict(data), _sensitive_keys(data)),
        }
    )
    return payload
