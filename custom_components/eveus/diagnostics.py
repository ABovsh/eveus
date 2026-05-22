"""Diagnostics support for Eveus."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import EveusConfigEntry

TO_REDACT = {"password", "username", "host", "unique_id"}
DEVICE_DIAGNOSTIC_KEYS = (
    "verFWMain",
    "verFWWifi",
    "state",
    "subState",
    "currentSet",
    "powerMeas",
    "voltMeas1",
    "curMeas1",
    "temperature1",
    "temperature2",
    "ground",
)


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
    payload.update(
        {
            "coordinator": {
                "last_update_success": updater.last_update_success,
                "update_interval": (
                    updater.update_interval.total_seconds()
                    if updater.update_interval is not None
                    else None
                ),
                "connection_quality": updater.connection_quality,
                "is_likely_offline": updater.is_likely_offline,
            },
            "device": {
                "firmware": data.get("verFWMain"),
                "wifi_firmware": data.get("verFWWifi"),
                "state": data.get("state"),
                "substate": data.get("subState"),
                "current_set": data.get("currentSet"),
                "sanitized_raw": {
                    key: data.get(key)
                    for key in DEVICE_DIAGNOSTIC_KEYS
                    if key in data
                },
            },
        }
    )
    return payload
