"""Regression tests for the 4.12.1 hardening round."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.conftest import (
    TEST_HOST,
    TEST_PASSWORD,
    TEST_USERNAME,
    EveusTestUpdater,
    disable_state_writes,
)

from custom_components.eveus import (
    _BatteryLowTracker,
    _update_battery_low_issue,
    async_migrate_entry,
    async_unload_entry,
)
from custom_components.eveus.const import (
    BATTERY_LOW_THRESHOLD_VOLTS,
    BATTERY_VBAT_MAX_PLAUSIBLE_VOLTS,
    DOMAIN,
)

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# F01/F02 — battery tracker: stale polls and corrupt readings
# ---------------------------------------------------------------------------


class _IssueRegistryRecorder:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.deleted: list[str] = []

    def async_create_issue(self, _hass, _domain, issue_id, **_kwargs) -> None:
        self.created.append(issue_id)

    def async_delete_issue(self, _hass, _domain, issue_id) -> None:
        self.deleted.append(issue_id)


def test_failed_poll_does_not_advance_battery_debounce(monkeypatch) -> None:
    from custom_components.eveus import ir

    recorder = _IssueRegistryRecorder()
    monkeypatch.setattr(ir, "async_create_issue", recorder.async_create_issue)
    monkeypatch.setattr(ir, "async_delete_issue", recorder.async_delete_issue)

    tracker = _BatteryLowTracker()
    entry = SimpleNamespace(entry_id="e1")
    low = BATTERY_LOW_THRESHOLD_VOLTS - 0.5

    # One genuine low reading.
    updater = EveusTestUpdater({"vBat": low})
    _update_battery_low_issue(None, entry, updater, tracker)

    # The charger drops offline; the coordinator keeps notifying listeners
    # with the stale payload. The debounce must not advance.
    updater.available = False
    updater.last_update_success = False
    for _ in range(10):
        _update_battery_low_issue(None, entry, updater, tracker)

    assert recorder.created == []
    assert tracker._low_streak == 1


def test_corrupt_high_vbat_does_not_clear_active_warning() -> None:
    tracker = _BatteryLowTracker()
    low = BATTERY_LOW_THRESHOLD_VOLTS - 0.5
    decisions = [tracker.evaluate(low) for _ in range(3)]
    assert decisions[-1] is True  # warning raised

    # An implausible finite spike must neither clear nor restart anything.
    assert tracker.evaluate(BATTERY_VBAT_MAX_PLAUSIBLE_VOLTS + 100.0) is None
    assert tracker._active is True


# ---------------------------------------------------------------------------
# F03 — unload must not delete issues when platform unload fails
# ---------------------------------------------------------------------------


def _unload_hass(unload_result: bool):
    async def _unload_platforms(_entry, _platforms) -> bool:
        return unload_result

    return SimpleNamespace(
        config_entries=SimpleNamespace(async_unload_platforms=_unload_platforms)
    )


@pytest.mark.parametrize("unload_result", [True, False])
def test_unload_deletes_issues_only_on_success(monkeypatch, unload_result) -> None:
    from custom_components.eveus import ir

    recorder = _IssueRegistryRecorder()
    monkeypatch.setattr(ir, "async_delete_issue", recorder.async_delete_issue)

    entry = SimpleNamespace(entry_id="e1")
    result = asyncio.run(async_unload_entry(_unload_hass(unload_result), entry))

    assert result is unload_result
    assert bool(recorder.deleted) is unload_result


# ---------------------------------------------------------------------------
# F04 — auth failures propagate from the Charging Current control
# ---------------------------------------------------------------------------


def test_set_current_propagates_auth_failure() -> None:
    from homeassistant.exceptions import ConfigEntryAuthFailed

    from custom_components.eveus.number import EveusCurrentNumber

    class _AuthFailUpdater(EveusTestUpdater):
        async def send_command(self, command, value, *, retry=True, extra=None):
            raise ConfigEntryAuthFailed("Eveus charger rejected credentials")

    entity = EveusCurrentNumber(_AuthFailUpdater({"currentSet": 10}), "16A")
    disable_state_writes(entity)

    with pytest.raises(ConfigEntryAuthFailed):
        asyncio.run(entity.async_set_native_value(12))


# ---------------------------------------------------------------------------
# F05/F06/F07 — connection-quality cache and monotonic ages
# ---------------------------------------------------------------------------


def _real_updater():
    from custom_components.eveus.common_network import EveusUpdater

    updater = EveusUpdater.__new__(EveusUpdater)
    updater._poll_results = []
    updater._latency_samples = []
    updater._consecutive_failures = 0
    updater._last_success_time = 0.0
    updater._last_success_monotonic = 0.0
    updater._last_error = None
    updater._connection_quality_cache = None
    updater._command_manager = SimpleNamespace(consecutive_failures=0)
    return updater


def test_is_healthy_recomputed_on_every_read() -> None:
    updater = _real_updater()
    updater._poll_results = [True] * 10
    updater._last_success_time = time.time()
    updater._last_success_monotonic = time.monotonic()

    assert updater.connection_quality["is_healthy"] is True

    # Success ages past the freshness threshold without any cache
    # invalidation: a cached snapshot must not keep reporting healthy.
    updater._last_success_monotonic = time.monotonic() - 301
    assert updater.connection_quality["is_healthy"] is False


def test_offline_detection_immune_to_backward_wall_clock() -> None:
    updater = _real_updater()
    updater._consecutive_failures = 11
    # Last success 700s ago on the monotonic clock, but wall clock stepped
    # far into the past (negative wall age must not mask the outage).
    updater._last_success_time = time.time() + 10_000
    updater._last_success_monotonic = time.monotonic() - 700

    assert updater.is_likely_offline is True


def test_seconds_since_success_inf_before_first_success() -> None:
    updater = _real_updater()
    assert updater._seconds_since_success() == float("inf")


# ---------------------------------------------------------------------------
# F10 — availability grace re-anchors on backward clock jumps
# ---------------------------------------------------------------------------


def _diag_sensor(updater):
    from custom_components.eveus.sensor_definitions import (
        OptimizedEveusSensor,
        SensorSpec,
        SensorType,
    )

    spec = SensorSpec(
        key="test_diag",
        name="Test Diag",
        value_fn=lambda _updater, _hass: 1,
        sensor_type=SensorType.DIAGNOSTIC,
    )
    sensor = OptimizedEveusSensor(updater, spec)
    disable_state_writes(sensor)
    return sensor


def test_negative_grace_age_reanchors_instead_of_lasting_forever() -> None:
    updater = EveusTestUpdater({}, available=False)
    sensor = _diag_sensor(updater)

    # Outage began "in the future" (wall clock stepped backward since).
    sensor._unavailable_since = time.time() + 10_000
    sensor._update_availability_state()

    # Re-anchored to now: still inside the grace window, not pinned forever.
    assert sensor._unavailable_since <= time.time() + 1
    assert sensor.available is True

    # And a properly expired grace still flips to unavailable.
    sensor._unavailable_since = time.time() - 10_000
    sensor._update_availability_state()
    assert sensor.available is False


# ---------------------------------------------------------------------------
# F11 — control fallback rejects negative read age
# ---------------------------------------------------------------------------


def test_control_fallback_rejects_future_read_timestamp() -> None:
    from custom_components.eveus.number import EveusCurrentNumber

    entity = EveusCurrentNumber(EveusTestUpdater({}, available=False), "16A")
    entity._last_device_value = 10.0
    entity._last_successful_read = time.time() + 10_000  # backward jump

    assert entity._resolve_value() is None


# ---------------------------------------------------------------------------
# F12 — config-flow SOC schemas reject booleans
# ---------------------------------------------------------------------------


def test_soc_schema_is_serializable_and_validates_range() -> None:
    # The SOC step schema must JSON-serialize for the frontend (issue #8) while
    # still rejecting out-of-range values.
    import homeassistant.helpers.config_validation as cv
    import voluptuous as vol
    import voluptuous_serialize

    from custom_components.eveus import config_flow as cf

    class _Hass:
        states = type("S", (), {"get": staticmethod(lambda eid: None)})()

    schema = cf.build_soc_step_schema(_Hass(), defaults={})
    voluptuous_serialize.convert(schema, custom_serializer=cv.custom_serializer)

    cap_lo, cap_hi = cf.SOC_INPUT_LIMITS["battery_capacity"]
    schema({"battery_capacity": cap_lo, "soc_correction": 0})  # valid submission
    with pytest.raises(vol.Invalid):
        schema({"battery_capacity": cap_hi + 1000, "soc_correction": 0})


# ---------------------------------------------------------------------------
# F14/F15 — migration canonicalizes bare hosts, without identity collisions
# ---------------------------------------------------------------------------


class _MigrationEntries:
    def __init__(self, others: list[SimpleNamespace] | None = None) -> None:
        self.updates: list[dict] = []
        self._others = others or []

    def async_entries(self, _domain=None):
        return self._others

    def async_update_entry(self, _entry, **kwargs) -> None:
        self.updates.append(kwargs)


def _migration_hass(entries: _MigrationEntries):
    return SimpleNamespace(
        config_entries=entries,
        states=SimpleNamespace(get=lambda _eid: None),
    )


def test_migration_canonicalizes_bare_uppercase_host(monkeypatch) -> None:
    monkeypatch.setattr(
        "custom_components.eveus.config_flow.migrate_device_identifiers",
        lambda *a, **k: None,
    )
    entries = _MigrationEntries()
    entry = SimpleNamespace(
        entry_id="e1",
        data={"host": "MYCHARGER.LOCAL.", "soc_mode": "basic"},
        unique_id="MYCHARGER.LOCAL.",
        title="Eveus Charger (MYCHARGER.LOCAL.)",
        version=1,
    )
    assert asyncio.run(async_migrate_entry(_migration_hass(entries), entry))

    (update,) = entries.updates
    assert update["data"]["host"] == "mycharger.local"
    assert update["unique_id"] == "mycharger.local"
    assert "mycharger.local" in update["title"]


def test_migration_skips_unique_id_rewrite_on_collision() -> None:
    other = SimpleNamespace(entry_id="e2", unique_id="mycharger.local")
    entries = _MigrationEntries([other])
    entry = SimpleNamespace(
        entry_id="e1",
        data={"host": "MYCHARGER.LOCAL", "soc_mode": "basic"},
        unique_id="MYCHARGER.LOCAL",
        title="Eveus Charger",
        version=1,
    )
    assert asyncio.run(async_migrate_entry(_migration_hass(entries), entry))

    (update,) = entries.updates
    assert update["data"]["host"] == "mycharger.local"
    assert "unique_id" not in update


# ---------------------------------------------------------------------------
# F16 — reconfigure migrates host-based device identifiers
# ---------------------------------------------------------------------------


def test_reconfigure_migrates_device_identifiers(monkeypatch) -> None:
    from custom_components.eveus import config_flow

    device = SimpleNamespace(
        id="dev1",
        identifiers={(DOMAIN, TEST_HOST), (DOMAIN, f"{TEST_HOST}_2"), ("other", "x")},
    )
    updated: list[tuple[str, set]] = []
    registry = SimpleNamespace(
        async_update_device=lambda dev_id, new_identifiers: updated.append(
            (dev_id, new_identifiers)
        )
    )
    monkeypatch.setattr(config_flow.dr, "async_get", lambda _hass: registry)
    monkeypatch.setattr(
        config_flow.dr,
        "async_entries_for_config_entry",
        lambda _reg, _eid: [device],
    )

    flow = config_flow.ConfigFlow()
    flow.hass = object()
    entry = SimpleNamespace(entry_id="e1")
    flow._migrate_device_identifiers(entry, TEST_HOST, "10.0.0.9")

    (dev_id, identifiers), = updated
    assert dev_id == "dev1"
    assert identifiers == {(DOMAIN, "10.0.0.9"), (DOMAIN, "10.0.0.9_2"), ("other", "x")}


# ---------------------------------------------------------------------------
# F17 — non-encodable credentials rejected up front
# ---------------------------------------------------------------------------


def test_credentials_must_be_latin1_encodable() -> None:
    import voluptuous as vol

    from custom_components.eveus.config_flow import validate_credentials

    with pytest.raises(vol.Invalid):
        validate_credentials(TEST_USERNAME, "пароль")
    assert validate_credentials(TEST_USERNAME, TEST_PASSWORD) == (
        TEST_USERNAME,
        TEST_PASSWORD,
    )


# ---------------------------------------------------------------------------
# F19 — diagnostics applies the name heuristic to entry data too
# ---------------------------------------------------------------------------


def test_diagnostics_redacts_heuristic_entry_keys() -> None:
    from custom_components.eveus.diagnostics import _sensitive_keys

    keys = _sensitive_keys({"host": "h", "api_token": "x", "model": "16A"})
    assert "api_token" in keys
    assert "model" not in keys


# ---------------------------------------------------------------------------
# F20 — device metadata refreshes after a firmware change
# ---------------------------------------------------------------------------


def test_device_info_refreshes_on_firmware_drift() -> None:
    updater = EveusTestUpdater({"verFWMain": "1.0"})
    sensor = _diag_sensor(updater)
    sensor._maybe_finalize_device_info()
    assert sensor._attr_device_info["sw_version"] == "1.0"
    assert sensor._device_info_finalized is True

    updater.data = {"verFWMain": "2.0"}
    sensor._maybe_finalize_device_info()
    assert sensor._attr_device_info["sw_version"] == "2.0"


# ---------------------------------------------------------------------------
# F21 — malformed serial values never reach the registry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_serial", [True, {"a": 1}, ["x"], ""])
def test_malformed_serial_is_dropped(bad_serial) -> None:
    from custom_components.eveus.utils import get_device_info

    info = get_device_info(TEST_HOST, {"serialNum": bad_serial}, 1)
    assert "serial_number" not in info


def test_valid_serial_is_kept() -> None:
    from custom_components.eveus.utils import get_device_info

    info = get_device_info(TEST_HOST, {"serialNum": " SN123 "}, 1)
    assert info["serial_number"] == "SN123"


# ---------------------------------------------------------------------------
# F22 — no ETA outside an active charging session
# ---------------------------------------------------------------------------


def test_eta_not_charging_when_state_inactive() -> None:
    from custom_components.eveus.ev_sensors import (
        CachedSOCCalculator,
        TimeToTargetSocSensor,
    )

    calc = CachedSOCCalculator()
    for key, value in (
        ("initial_soc", 20.0),
        ("battery_capacity", 80.0),
        ("soc_correction", 10.0),
        ("target_soc", 80.0),
    ):
        calc.set_value(key, value)

    # Residual standby power in a non-charging state must not fabricate an ETA.
    sensor = TimeToTargetSocSensor(
        EveusTestUpdater({"sessionEnergy": "10", "powerMeas": "50", "state": 2}),
        1,
        calc,
    )
    assert sensor._get_sensor_value() == "Not charging"


# ---------------------------------------------------------------------------
# C31 — session-time attribute mirrors the state's bounds
# ---------------------------------------------------------------------------


def test_session_time_attrs_reject_absurd_duration() -> None:
    from custom_components.eveus.const import MAX_SESSION_TIME_SECONDS
    from custom_components.eveus.sensor_definitions import get_session_time_attrs

    updater = EveusTestUpdater({"sessionTime": MAX_SESSION_TIME_SECONDS + 1})
    assert get_session_time_attrs(updater, None) == {}

    updater = EveusTestUpdater({"sessionTime": 3600})
    assert get_session_time_attrs(updater, None) == {"duration_seconds": 3600}


# ---------------------------------------------------------------------------
# C61 — repair fix-flow translates the duplicate-address error
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "custom_components/eveus/strings.json",
        "custom_components/eveus/translations/en.json",
        "custom_components/eveus/translations/uk.json",
    ],
)
def test_repair_fix_flow_has_already_configured_error(path: str) -> None:
    data = json.loads((ROOT / path).read_text())
    errors = data["issues"]["invalid_config"]["fix_flow"]["error"]
    assert "already_configured" in errors
