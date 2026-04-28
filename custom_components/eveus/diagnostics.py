"""Diagnostics support for Eveus."""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import EveusConfigEntry

TO_REDACT = {"password", "username"}


def _redact(data: dict[str, Any]) -> dict[str, Any]:
    """Return a redacted copy of a mapping."""
    return {
        key: "**REDACTED**" if key in TO_REDACT else value
        for key, value in data.items()
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: EveusConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime_data = entry.runtime_data
    updater = runtime_data.updater
    data = updater.data or {}

    return {
        "entry": {
            "title": entry.title,
            "data": _redact(dict(entry.data)),
            "device_number": runtime_data.device_number,
        },
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
        },
    }
