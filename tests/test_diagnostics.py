"""Unit tests for diagnostics output."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
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
        data={"host": TEST_HOST, "username": TEST_USERNAME, "password": TEST_PASSWORD},
        runtime_data=SimpleNamespace(updater=updater, device_number=1),
    )

    diagnostics = asyncio.run(async_get_config_entry_diagnostics(None, entry))

    assert diagnostics["entry"]["data"] == {
        "host": "**REDACTED**",
        "username": "**REDACTED**",
        "password": "**REDACTED**",
    }
    assert diagnostics["coordinator"]["last_update_success"] is True
    assert diagnostics["coordinator"]["update_interval"] == 30
    assert diagnostics["device"]["firmware"] == "3.0.3"
    assert diagnostics["device"]["sanitized_raw"] == {
        "verFWMain": "3.0.3",
        "verFWWifi": "1.0.0",
        "state": 4,
        "subState": 1,
        "currentSet": 16,
    }


def test_diagnostics_returns_partial_payload_when_runtime_data_missing() -> None:
    """Diagnostics must not raise when setup failed before runtime_data was set."""
    entry = SimpleNamespace(
        title="Eveus Charger",
        data={"host": TEST_HOST, "username": TEST_USERNAME, "password": TEST_PASSWORD},
    )

    diagnostics = asyncio.run(async_get_config_entry_diagnostics(None, entry))

    assert diagnostics["entry"]["device_number"] is None
    assert diagnostics["setup"]["ready"] is False
    assert "coordinator" not in diagnostics
    assert "device" not in diagnostics


def test_diagnostics_handles_missing_device_data_and_update_interval() -> None:
    updater = SimpleNamespace(
        data=None,
        last_update_success=False,
        update_interval=None,
        connection_quality={
            "success_rate": 25,
            "last_error": "TimeoutError",
        },
        is_likely_offline=True,
    )
    entry = SimpleNamespace(
        title="Eveus Charger",
        data={"host": TEST_HOST, "username": TEST_USERNAME, "password": TEST_PASSWORD},
        runtime_data=SimpleNamespace(updater=updater, device_number=2),
    )

    diagnostics = asyncio.run(async_get_config_entry_diagnostics(None, entry))

    assert diagnostics["entry"]["device_number"] == 2
    assert diagnostics["coordinator"]["update_interval"] is None
    assert diagnostics["coordinator"]["is_likely_offline"] is True
    assert diagnostics["device"] == {
        "firmware": None,
        "wifi_firmware": None,
        "state": None,
        "substate": None,
        "current_set": None,
        "sanitized_raw": {},
    }


def test_diagnostics_does_not_leak_host_via_title() -> None:
    """Diagnostics title must not echo the configured host even though the title contains it."""
    entry = SimpleNamespace(
        title=f"Eveus Charger ({TEST_HOST})",
        data={"host": TEST_HOST, "username": "u", "password": "p"},
        runtime_data=None,
    )
    payload = asyncio.run(async_get_config_entry_diagnostics(object(), entry))
    assert TEST_HOST not in payload["entry"]["title"]
