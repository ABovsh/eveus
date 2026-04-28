"""Unit tests for diagnostics output."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

from custom_components.eveus.diagnostics import async_get_config_entry_diagnostics


def test_diagnostics_redacts_credentials_and_reports_coordinator_state() -> None:
    updater = SimpleNamespace(
        data={
            "verFWMain": "3.0.3",
            "verFWWifi": "1.0.0",
            "state": 4,
            "subState": 1,
            "currentSet": 16,
        },
        last_update_success=True,
        update_interval=timedelta(seconds=30),
        connection_quality={"success_rate": 100},
        is_likely_offline=False,
    )
    entry = SimpleNamespace(
        title="Eveus Charger",
        data={"host": "192.168.1.50", "username": "admin", "password": "secret"},
        runtime_data=SimpleNamespace(updater=updater, device_number=1),
    )

    diagnostics = asyncio.run(async_get_config_entry_diagnostics(None, entry))

    assert diagnostics["entry"]["data"] == {
        "host": "192.168.1.50",
        "username": "**REDACTED**",
        "password": "**REDACTED**",
    }
    assert diagnostics["coordinator"]["last_update_success"] is True
    assert diagnostics["coordinator"]["update_interval"] == 30
    assert diagnostics["device"]["firmware"] == "3.0.3"
