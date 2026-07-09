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
    assert diagnostics["raw_main"] == {
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
        "legacy_raw_state": None,
        "substate": None,
        "current_set": None,
        "model": None,
        "manufacturer": None,
    }
    assert diagnostics["raw_main"] == {}


def test_diagnostics_does_not_leak_host_via_title() -> None:
    """Diagnostics title must not echo the configured host even though the title contains it."""
    entry = SimpleNamespace(
        title=f"Eveus Charger ({TEST_HOST})",
        data={"host": TEST_HOST, "username": "u", "password": "p"},
        runtime_data=None,
    )
    payload = asyncio.run(async_get_config_entry_diagnostics(object(), entry))
    assert TEST_HOST not in payload["entry"]["title"]


from custom_components.eveus import diagnostics as diag


def test_sensitive_keys_flags_identifying_fields_only() -> None:
    data = {
        "state": 2,
        "currentSet": 16,
        "powerMeas": 1500,
        "sessionEnergy": 4.2,
        "IEM1_money": 10,
        "tarifAValue": 432,
        "wifiSSID": "MyHomeNet",  # NOSONAR - test fixture, not a real SSID
        "MACaddr": "aa:bb:cc:dd:ee:ff",  # NOSONAR - test fixture
        "STA_IP_Addres": "10.0.0.5",  # NOSONAR(python:S1313) - test fixture LAN
        "deviceToken": "abc123",  # NOSONAR - test fixture token
    }
    flagged = diag._sensitive_keys(data)

    assert {"wifiSSID", "MACaddr", "STA_IP_Addres", "deviceToken"} <= flagged
    assert flagged.isdisjoint(
        {"state", "currentSet", "powerMeas", "sessionEnergy", "IEM1_money", "tarifAValue"}
    )


def test_sensitive_keys_walks_nested_structures() -> None:
    data = {
        "powerMeas": 7200,
        "nested": {"deep": {"wifi_ssid": "x"}, "list": [{"device_mac": "y"}]},
    }
    keys = diag._sensitive_keys(data)
    assert "wifi_ssid" in keys
    assert "device_mac" in keys
    assert "powerMeas" not in keys


def test_diagnostics_redacts_heuristic_entry_keys() -> None:
    keys = diag._sensitive_keys({"host": "h", "api_token": "x", "model": "16A"})
    assert "api_token" in keys
    assert "model" not in keys


def test_diagnostics_heuristic_redacts_credential_like_keys() -> None:
    data = {
        "api_key": "x",
        "authorization": "x",
        "credentials": {"private_key": "x"},
        "pwd": "x",
        "battery_capacity": 80,
        "phases": 3,
    }
    keys = diag._sensitive_keys(data)
    for k in ("api_key", "authorization", "credentials", "private_key", "pwd"):
        assert k in keys, k
    assert "battery_capacity" not in keys
    assert "phases" not in keys


def test_update_failed_messages_do_not_contain_host():
    from custom_components.eveus import common_network

    src = common_network.__file__
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    assert 'UpdateFailed(f"Skipping poll for {self.host}' not in text
    assert 'UpdateFailed(f"Connection issue with {self.host}' not in text
    assert 'UpdateFailed(f"Invalid response from {self.host}' not in text


def test_offline_log_messages_do_not_contain_host():
    from custom_components.eveus import common_network

    src = common_network.__file__
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    assert "Device %s appears offline" not in text
    assert "Connection issue with %s" not in text


def test_init_device_number_log_does_not_include_host():
    import custom_components.eveus as ev_pkg

    src = ev_pkg.__file__
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    assert "Assigned device number %d to %s" not in text
    assert "Normalized device number %d for %s" not in text


def test_config_flow_exception_does_not_stringify_aiohttp_error():
    from custom_components.eveus import config_flow

    src = config_flow.__file__
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    assert 'CannotConnect(f"Connection error: {err}")' not in text
    assert 'CannotConnect(f"Unexpected error: {err}")' not in text


def test_translations_cover_clock_drift_in_en_and_uk():
    import json
    from pathlib import Path

    base = Path("custom_components/eveus")
    for name in ("translations/en.json", "translations/uk.json", "strings.json"):
        issues = json.loads((base / name).read_text())["issues"]
        assert "clock_drift" in issues, name
        assert "Sync Time" in issues["clock_drift"]["description"] or \
            "Синхрон" in issues["clock_drift"]["description"], name


def test_fractional_timezone_message_exists_in_all_locales():
    import json
    from pathlib import Path

    base = Path("custom_components/eveus")
    for name in ("strings.json", "translations/en.json", "translations/uk.json"):
        issues = json.loads((base / name).read_text())["issues"]
        assert "clock_drift_fractional_timezone" in issues, name


def test_soc_notice_does_not_hardcode_first_charger_prefix() -> None:
    import json
    from pathlib import Path

    base = Path("custom_components/eveus")
    for name in ("strings.json", "translations/en.json", "translations/uk.json"):
        desc = json.loads((base / name).read_text())["issues"]["soc_dashboard_update"][
            "description"
        ]
        assert "number.eveus_ev_charger_" not in desc, name


def test_force_refresh_button_raises_on_failed_poll() -> None:
    import asyncio
    import pytest
    from homeassistant.exceptions import HomeAssistantError
    from conftest import EveusTestUpdater
    from custom_components.eveus.button import EveusRefreshButton

    updater = EveusTestUpdater({})

    async def fake_force_refresh():
        return None

    updater.async_force_refresh = fake_force_refresh
    updater.last_update_success = False
    button = EveusRefreshButton(updater)
    with pytest.raises(HomeAssistantError):
        asyncio.run(button.async_press())

    updater.last_update_success = True
    asyncio.run(button.async_press())  # no raise


def test_clock_drift_issue_rekeys_when_kind_changes(monkeypatch) -> None:
    import time
    from datetime import timedelta, timezone as _tz
    from types import SimpleNamespace
    from homeassistant.util import dt as dt_util
    from custom_components import eveus

    # Clock-drift maths compare wall clocks; pin HA's local offset to +3.
    original = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(_tz(timedelta(hours=3)))
    try:
        created: list[dict] = []
        monkeypatch.setattr(
            eveus.ir,
            "async_create_issue",
            lambda hass, domain, issue_id, **kw: created.append(kw),
        )
        entry = SimpleNamespace(entry_id="e1")
        tracker = eveus._ClockDriftTracker()
        updater = SimpleNamespace(available=True, last_update_success=True, data=None)

        def _drift_payload(drift_seconds: int) -> dict:
            return {
                "systemTime": int(time.time()) + 3 * 3600 + drift_seconds,
                "timeZone": "3",
            }

        for _ in range(3):
            updater.data = _drift_payload(900)
            eveus._update_clock_drift_issue(object(), entry, updater, tracker)
        assert created[-1]["translation_key"] == "clock_drift"

        for _ in range(3):
            updater.data = _drift_payload(-3600)
            eveus._update_clock_drift_issue(object(), entry, updater, tracker)
        assert created[-1]["translation_key"] == "clock_drift_timezone"
        assert created[-1]["translation_placeholders"] == {"hours": "1"}
    finally:
        dt_util.set_default_time_zone(original)


def test_fractional_timezone_raises_unsupported_message(monkeypatch) -> None:
    import time
    from datetime import timedelta, timezone as _tz
    from types import SimpleNamespace
    from homeassistant.util import dt as dt_util
    from custom_components import eveus

    dt_util.set_default_time_zone(_tz(timedelta(hours=5, minutes=30)))  # India
    created: list[dict] = []
    monkeypatch.setattr(
        eveus.ir,
        "async_create_issue",
        lambda hass, domain, issue_id, **kw: created.append(kw),
    )
    entry = SimpleNamespace(entry_id="e1")
    tracker = eveus._ClockDriftTracker()
    updater = SimpleNamespace(available=True, last_update_success=True, data=None)

    for _ in range(4):
        updater.data = {
            "systemTime": str(int(time.time()) + 5 * 3600),
            "timeZone": "5",
        }
        eveus._update_clock_drift_issue(object(), entry, updater, tracker)

    # Restore original timezone
    from homeassistant.util import dt as dt_util2
    dt_util2.set_default_time_zone(_tz(timedelta(hours=0)))

    assert created, "fractional-offset drift must still raise a notice"
    assert created[-1]["translation_key"] == "clock_drift_fractional_timezone"


def test_clock_drift_rekey_requires_stable_classification(monkeypatch) -> None:
    import time
    from datetime import timedelta, timezone as _tz
    from types import SimpleNamespace
    from homeassistant.util import dt as dt_util
    from custom_components import eveus

    original = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(_tz(timedelta(hours=3)))
    try:
        created: list[dict] = []
        monkeypatch.setattr(
            eveus.ir,
            "async_create_issue",
            lambda hass, domain, issue_id, **kw: created.append(kw),
        )
        entry = SimpleNamespace(entry_id="e1")
        tracker = eveus._ClockDriftTracker()
        updater = SimpleNamespace(available=True, last_update_success=True, data=None)

        def _drift_payload(drift_seconds: int) -> dict:
            return {
                "systemTime": int(time.time()) + 3 * 3600 + drift_seconds,
                "timeZone": "3",
            }

        for _ in range(3):
            updater.data = _drift_payload(900)
            eveus._update_clock_drift_issue(object(), entry, updater, tracker)
        base_count = len(created)

        for offset in (3300, 3299, 3300, 3299):
            updater.data = _drift_payload(offset)
            eveus._update_clock_drift_issue(object(), entry, updater, tracker)
        assert len(created) == base_count

        for _ in range(3):
            updater.data = _drift_payload(3600)
            eveus._update_clock_drift_issue(object(), entry, updater, tracker)
        assert len(created) == base_count + 1
        assert created[-1]["translation_key"] == "clock_drift_timezone"
    finally:
        dt_util.set_default_time_zone(original)


def test_diagnostics_reports_init_firmware_fallback_and_legacy_raw_state() -> None:
    """Firmware 1.x: /main has no verFWMain (version comes from /init), and the
    coordinator stores the original legacy state code under a synthetic key
    that must not masquerade as a device-reported field in raw_main."""
    updater = SimpleNamespace(
        data={"state": 2, "_legacy_raw_state": 20, "currentSet": 7},
        _init_fw_fallback="1.51",
        last_update_success=True,
        update_interval=timedelta(seconds=60),
        connection_quality={"success_rate": 100},
        is_likely_offline=False,
    )
    entry = SimpleNamespace(
        title="Eveus Charger",
        data={"host": TEST_HOST, "username": TEST_USERNAME, "password": TEST_PASSWORD},
        runtime_data=SimpleNamespace(updater=updater, device_number=1),
    )

    diagnostics = asyncio.run(async_get_config_entry_diagnostics(None, entry))

    assert diagnostics["device"]["firmware"] == "1.51"
    assert diagnostics["device"]["legacy_raw_state"] == 20
    assert "_legacy_raw_state" not in diagnostics["raw_main"]
    assert diagnostics["raw_main"]["state"] == 2


def test_diagnostics_prefers_main_firmware_over_init_fallback() -> None:
    updater = SimpleNamespace(
        data={"verFWMain": "3.0.3", "state": 4},
        _init_fw_fallback=None,
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

    assert diagnostics["device"]["firmware"] == "3.0.3"
    assert diagnostics["device"]["legacy_raw_state"] is None
