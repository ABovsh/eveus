"""Hardening round: verified audit findings for the next release."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from homeassistant.exceptions import HomeAssistantError

from conftest import (
    TEST_HOST,
    TEST_PASSWORD,
    TEST_USERNAME,
    EveusTestUpdater,
    HelperHass,
    disable_state_writes,
)
from custom_components.eveus import _payload
from custom_components.eveus import config_flow
from custom_components.eveus.const import MODEL_16A, MODEL_32A, MODEL_MAX_CURRENT
from custom_components.eveus.utils import _safe_str, get_device_info


@pytest.fixture(autouse=True)
def _ha_local_clock_utc_plus_3():
    from datetime import timedelta, timezone as _tz

    from homeassistant.util import dt as dt_util

    original = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(_tz(timedelta(hours=3)))
    yield
    dt_util.set_default_time_zone(original)


# --- B-F04: poll validator rejects currentSet above every supported model ---


def test_payload_rejects_current_above_global_ceiling_without_model() -> None:
    with pytest.raises(_payload.PayloadError):
        _payload.validate_main_payload({"state": 2, "currentSet": 999})


def test_payload_accepts_max_supported_current_without_model() -> None:
    top = max(MODEL_MAX_CURRENT.values())
    payload = {"state": 2, "currentSet": top}
    assert _payload.validate_main_payload(payload) is payload


# --- A-F01: invalid stored phases must not prune three-phase entities ---


def test_resolve_phases_flags_invalid_values() -> None:
    from custom_components.eveus import _resolve_phases

    assert _resolve_phases(3) == (3, False)
    assert _resolve_phases("3") == (3, False)
    assert _resolve_phases(1) == (1, False)
    assert _resolve_phases("garbage") == (1, True)
    assert _resolve_phases(2) == (1, True)
    assert _resolve_phases(None) == (1, True)


# --- A-F03: removing the entry deletes every per-entry repair issue ---


def test_async_remove_entry_deletes_all_per_entry_issues(monkeypatch) -> None:
    from custom_components import eveus

    deleted: list[str] = []
    monkeypatch.setattr(
        eveus.ir,
        "async_delete_issue",
        lambda hass, domain, issue_id: deleted.append(issue_id),
    )
    entry = SimpleNamespace(entry_id="e1")
    asyncio.run(eveus.async_remove_entry(object(), entry))

    for expected in (
        "invalid_config_e1",
        "ocpp_enabled_e1",
        "battery_low_e1",
        "clock_drift_e1",
        "soc_dashboard_update_e1",
    ):
        assert expected in deleted, expected
    # every safety policy issue is cleared too
    from custom_components.eveus.safety import POLICIES

    for policy in POLICIES:
        assert f"safety_{policy.key}_e1" in deleted, policy.key


# --- A-F04: active clock-drift repair re-keys when the classification changes ---


def _drift_payload(drift_seconds: int) -> dict:
    return {
        "systemTime": int(time.time()) + 3 * 3600 + drift_seconds,
        "timeZone": "3",
    }


def test_clock_drift_issue_rekeys_when_kind_changes(monkeypatch) -> None:
    from custom_components import eveus

    created: list[dict] = []
    monkeypatch.setattr(
        eveus.ir,
        "async_create_issue",
        lambda hass, domain, issue_id, **kw: created.append(kw),
    )
    entry = SimpleNamespace(entry_id="e1")
    tracker = eveus._ClockDriftTracker()
    updater = SimpleNamespace(available=True, last_update_success=True, data=None)

    for _ in range(3):
        updater.data = _drift_payload(900)
        eveus._update_clock_drift_issue(object(), entry, updater, tracker)
    assert created[-1]["translation_key"] == "clock_drift"

    # drift morphs to a whole hour while the issue is active; the message
    # switches after the classification holds for the debounce streak
    for _ in range(3):
        updater.data = _drift_payload(-3600)
        eveus._update_clock_drift_issue(object(), entry, updater, tracker)
    assert created[-1]["translation_key"] == "clock_drift_timezone"
    assert created[-1]["translation_placeholders"] == {"hours": "1"}


# --- A-F05: a partial payload must not downgrade finalized device metadata ---


def test_finalized_metadata_survives_fallback_fields() -> None:
    from custom_components.eveus.common_base import _preserve_finalized_metadata

    old = {
        "model": "Eveus Pro 32A",
        "manufacturer": "Eveus Ltd",
        "sw_version": "R3.05.2",
        "serial_number": "SN123",
    }
    new = {
        "model": "Eveus EV Charger",  # fallback
        "manufacturer": "Eveus",  # fallback
        "sw_version": "R3.05.3",
    }
    merged = _preserve_finalized_metadata(old, new)
    assert merged["model"] == "Eveus Pro 32A"
    assert merged["manufacturer"] == "Eveus Ltd"
    assert merged["sw_version"] == "R3.05.3"
    assert merged["serial_number"] == "SN123"


# --- B-F01: Connection Quality stays readable while polls fail ---


def test_connection_quality_reports_during_failures() -> None:
    import custom_components.eveus.sensor_definitions as sd

    specs = {s.key: s for s in sd.get_sensor_specifications()}
    spec = specs["connection_quality"]
    assert spec.available_when_offline is True

    updater = SimpleNamespace(
        host=TEST_HOST,
        available=False,
        last_update_success=False,
        data={},
        connection_quality={"success_rate": 40, "latency_avg": 1.2},
        async_add_listener=lambda *a, **k: (lambda: None),
    )
    entity = spec.create_sensor(updater)
    assert entity.available is True
    assert entity._get_sensor_value() == 40


# --- B-F02: derived SOC sensors do not recompute from stale data ---


def test_ev_sensor_skips_value_recompute_on_failed_poll() -> None:
    from custom_components.eveus.ev_sensors import EVSocKwhSensor, CachedSOCCalculator

    updater = EveusTestUpdater({"IEM1": "5"})
    calc = CachedSOCCalculator()
    sensor = EVSocKwhSensor(updater, calc)
    disable_state_writes(sensor)

    calls = []
    sensor._update_native_value = lambda: calls.append("value") or False

    updater.available = False
    updater.last_update_success = False
    sensor._handle_coordinator_update()
    assert calls == []

    updater.available = True
    updater.last_update_success = True
    sensor._handle_coordinator_update()
    assert calls == ["value"]


# --- B-F05: Session Active is unknown in charger error state ---


def test_session_active_unknown_in_error_state() -> None:
    from custom_components.eveus.binary_sensor import _session_active_is_on

    assert _session_active_is_on({"state": 7}) is None
    assert _session_active_is_on({"state": 4}) is True
    assert _session_active_is_on({"state": 2}) is False


# --- B-F06: non-finite metadata never reaches the device registry ---


def test_safe_str_rejects_non_finite_floats() -> None:
    assert _safe_str(float("nan")) == "Unknown"
    assert _safe_str(float("inf")) == "Unknown"
    assert _safe_str(3.5) == "3.5"


# --- B-F07: a corrupt primary alias falls back to the valid secondary ---


def test_device_info_alias_fallback_survives_corrupt_primary() -> None:
    info = get_device_info(
        TEST_HOST,
        {"verFWMain": True, "firmware": "1.2.3", "serialNum": {}, "stationId": "ST99"},
    )
    assert info["sw_version"] == "1.2.3"
    assert info["serial_number"] == "ST99"


# --- C-F03: reauth must not roll back concurrent entry-data changes ---


def test_reauth_rebases_on_live_entry_data(monkeypatch) -> None:
    entry = SimpleNamespace(
        data={
            "host": TEST_HOST,
            "username": "old",
            "password": "old",
            "model": MODEL_16A,
        },
        unique_id=TEST_HOST,
        title="Eveus",
    )

    async def fake_validate_input(hass, data):
        # a concurrent options flow commits a model change mid-validation
        entry.data = {**entry.data, "model": MODEL_32A}
        return {
            "title": f"Eveus Charger ({TEST_HOST})",
            "data": config_flow.normalize_user_input(
                {**data, "model": data.get("model", MODEL_16A)}
            ),
            "device_info": {"current_set": 16},
        }

    flow = config_flow.ConfigFlow()
    flow.hass = object()
    flow._get_reauth_entry = lambda: entry
    flow.async_set_unique_id = lambda unique_id: asyncio.sleep(0)
    captured = {}
    flow.async_update_reload_and_abort = lambda entry, **kw: captured.update(kw) or {
        "type": "abort",
        "reason": "reauth_successful",
    }
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    asyncio.run(
        flow.async_step_reauth_confirm(
            {"username": TEST_USERNAME, "password": TEST_PASSWORD}
        )
    )
    assert captured["data"]["model"] == MODEL_32A  # concurrent change preserved
    assert captured["data"]["username"] == TEST_USERNAME
    assert captured["data"]["password"] == TEST_PASSWORD


# --- C-F05: the repair flow migrates device identifiers on a host change ---


def test_repair_flow_migrates_device_identifiers(monkeypatch) -> None:
    from custom_components.eveus import repairs

    async def fake_validate_input(hass, data):
        return {
            "title": "Eveus Charger (newhost.local)",
            "data": config_flow.normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    migrated: list[tuple[str, str]] = []
    monkeypatch.setattr(repairs, "validate_input", fake_validate_input)
    monkeypatch.setattr(
        repairs,
        "migrate_device_identifiers",
        lambda hass, entry, old, new: migrated.append((old, new)),
    )
    monkeypatch.setattr(
        repairs.ir, "async_delete_issue", lambda *a, **k: None
    )

    class _Entries:
        updated: list = []

        def async_get_entry(self, entry_id):
            return entry

        def async_update_entry(self, entry, **kw):
            self.updated.append(kw)

        async def async_reload(self, entry_id):
            return None

        def async_entries(self, domain):
            return [entry]

    entry = SimpleNamespace(entry_id="e1", unique_id=TEST_HOST, data={
        "host": TEST_HOST,
        "username": TEST_USERNAME,
        "password": TEST_PASSWORD,
        "model": MODEL_16A,
    })
    hass = SimpleNamespace(config_entries=_Entries())

    flow = repairs.InvalidConfigRepairFlow(hass, "invalid_config_e1", "e1")
    asyncio.run(
        flow.async_step_confirm(
            {
                "host": "newhost.local",
                "username": TEST_USERNAME,
                "password": TEST_PASSWORD,
                "model": MODEL_16A,
            }
        )
    )
    assert migrated == [(TEST_HOST, "newhost.local")]


# --- C-F06: the SOC migration notice must not prescribe first-charger IDs ---


def test_soc_notice_does_not_hardcode_first_charger_prefix() -> None:
    base = Path("custom_components/eveus")
    for name in ("strings.json", "translations/en.json", "translations/uk.json"):
        desc = json.loads((base / name).read_text())["issues"]["soc_dashboard_update"][
            "description"
        ]
        assert "number.eveus_ev_charger_" not in desc, name


# --- D-F04: Force Refresh surfaces a failed poll ---


def test_force_refresh_button_raises_on_failed_poll() -> None:
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


# --- D-F02: a corrupt restored SOC value cannot break entity setup ---


def test_soc_number_survives_corrupt_restore_value(monkeypatch) -> None:
    from custom_components.eveus.ev_sensors import CachedSOCCalculator
    from custom_components.eveus.number import EveusBatteryCapacityNumber
    from custom_components.eveus import number as number_module

    updater = EveusTestUpdater({})
    calc = CachedSOCCalculator()
    entity = EveusBatteryCapacityNumber(updater, calc, seed=50, device_number=1)
    entity.hass = HelperHass({})
    disable_state_writes(entity)
    monkeypatch.setattr(number_module, "async_dispatcher_send", lambda *a, **k: None)

    async def corrupt_last_number_data():
        return SimpleNamespace(native_value="garbage")

    entity.async_get_last_number_data = corrupt_last_number_data

    async def no_super_restore():
        return None

    monkeypatch.setattr(
        type(entity).__mro__[1], "async_added_to_hass", lambda self: asyncio.sleep(0)
    )
    asyncio.run(entity.async_added_to_hass())
    assert entity.native_value == 50  # seed kept, no TypeError


# --- live-hardware finding: sub-minimum currentSet is a REAL firmware state ---
# Verified on R3.05.2 while charging: the firmware clamps OVER-max setpoints to
# curDesign, but accepts setpoints below its advertised minCurrent verbatim
# (delivery floors at ~6 A, the IEC 61851 minimum). /main can therefore
# legitimately report currentSet 1..6 and the poll validator must not fail the
# whole device over it.


def test_payload_accepts_sub_minimum_current() -> None:
    for amps in (1, 3, 5, 6):
        payload = {"state": 4, "currentSet": amps}
        assert _payload.validate_main_payload(payload) is payload


def test_payload_still_rejects_negative_current() -> None:
    with pytest.raises(_payload.PayloadError):
        _payload.validate_main_payload({"state": 4, "currentSet": -1})


# --- adversarial round: R-F01..R-F04 ---


def test_resolve_phases_rejects_boolean() -> None:
    from custom_components.eveus import _resolve_phases

    # bool is an int subclass: int(True)=1 would otherwise pass as valid and
    # drive the destructive phase prune.
    assert _resolve_phases(True) == (1, True)
    assert _resolve_phases(False) == (1, True)


def test_reauth_revalidates_when_host_changes_mid_flight(monkeypatch) -> None:
    calls: list[str] = []

    entry = SimpleNamespace(
        data={
            "host": TEST_HOST,
            "username": "old",
            "password": "old",
            "model": MODEL_16A,
        },
        unique_id=TEST_HOST,
        title="Eveus",
    )

    async def fake_validate_input(hass, data):
        calls.append(data["host"])
        if len(calls) == 1:
            # a concurrent reconfigure commits a host change mid-validation
            entry.data = {**entry.data, "host": "newhost.local"}
            entry.unique_id = "newhost.local"
        return {
            "title": f"Eveus Charger ({data['host']})",
            "data": config_flow.normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    flow = config_flow.ConfigFlow()
    flow.hass = object()
    flow._get_reauth_entry = lambda: entry
    flow.async_set_unique_id = lambda unique_id: asyncio.sleep(0)
    captured = {}
    flow.async_update_reload_and_abort = lambda entry, **kw: captured.update(kw) or {
        "type": "abort",
        "reason": "reauth_successful",
    }
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    asyncio.run(
        flow.async_step_reauth_confirm(
            {"username": TEST_USERNAME, "password": TEST_PASSWORD}
        )
    )
    # credentials were re-validated against the live (new) host before commit
    assert calls == [TEST_HOST, "newhost.local"]
    assert captured["data"]["host"] == "newhost.local"
    assert captured["data"]["username"] == TEST_USERNAME


def test_fractional_timezone_raises_unsupported_message(monkeypatch) -> None:
    from datetime import timedelta, timezone as _tz

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

    # charger synced perfectly with its best achievable whole-hour tz (+5):
    # wall clock is 30 min behind HA local — the closest the hardware can get.
    for _ in range(4):
        updater.data = {
            "systemTime": str(int(time.time()) + 5 * 3600),
            "timeZone": "5",
        }
        eveus._update_clock_drift_issue(object(), entry, updater, tracker)
    assert created, "fractional-offset drift must still raise a notice"
    assert created[-1]["translation_key"] == "clock_drift_fractional_timezone"


def test_fractional_timezone_message_exists_in_all_locales() -> None:
    base = Path("custom_components/eveus")
    for name in ("strings.json", "translations/en.json", "translations/uk.json"):
        issues = json.loads((base / name).read_text())["issues"]
        assert "clock_drift_fractional_timezone" in issues, name


def test_clock_drift_rekey_requires_stable_classification(monkeypatch) -> None:
    from custom_components import eveus

    created: list[dict] = []
    monkeypatch.setattr(
        eveus.ir,
        "async_create_issue",
        lambda hass, domain, issue_id, **kw: created.append(kw),
    )
    entry = SimpleNamespace(entry_id="e1")
    tracker = eveus._ClockDriftTracker()
    updater = SimpleNamespace(available=True, last_update_success=True, data=None)

    for _ in range(3):
        updater.data = _drift_payload(900)
        eveus._update_clock_drift_issue(object(), entry, updater, tracker)
    base_count = len(created)

    # oscillation across the whole-hour classification boundary must not
    # re-key on every poll
    for offset in (3300, 3299, 3300, 3299):
        updater.data = _drift_payload(offset)
        eveus._update_clock_drift_issue(object(), entry, updater, tracker)
    assert len(created) == base_count

    # a stable new classification re-keys after the debounce
    for _ in range(3):
        updater.data = _drift_payload(3600)
        eveus._update_clock_drift_issue(object(), entry, updater, tracker)
    assert len(created) == base_count + 1
    assert created[-1]["translation_key"] == "clock_drift_timezone"
