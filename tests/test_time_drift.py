"""Tests for the Time Drift diagnostic sensor replacing System Time."""
from __future__ import annotations

import time
from datetime import timedelta, timezone
from types import SimpleNamespace

import pytest
from homeassistant.util import dt as dt_util

import custom_components.eveus.sensor_definitions as sd
from custom_components.eveus.utils import get_charger_wall_clock_seconds

TZ_HOURS = 3
TZ_SHIFT = TZ_HOURS * 3600


@pytest.fixture(autouse=True)
def _ha_in_kyiv_summer():
    """Pin HA's local clock to UTC+3 (Kyiv summer time) for every test."""
    original = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(timezone(timedelta(hours=TZ_HOURS)))
    yield
    dt_util.set_default_time_zone(original)


def _updater(offset_seconds: int | None = None, **overrides):
    """Updater whose charger clock is `offset_seconds` away from real time."""
    data: dict[str, object] = {"timeZone": str(TZ_HOURS)}
    if offset_seconds is not None:
        data["systemTime"] = str(int(time.time()) + TZ_SHIFT + offset_seconds)
    data.update(overrides)
    return SimpleNamespace(available=True, data=data)


# --- get_charger_wall_clock_seconds (shared helper) ---


def test_charger_wall_clock_returns_validated_system_time() -> None:
    now = int(time.time())
    data = {"systemTime": str(now + TZ_SHIFT), "timeZone": str(TZ_HOURS)}
    assert get_charger_wall_clock_seconds(data) == now + TZ_SHIFT


def test_charger_wall_clock_requires_both_fields() -> None:
    now = int(time.time())
    assert get_charger_wall_clock_seconds({"systemTime": str(now)}) is None
    assert get_charger_wall_clock_seconds({"timeZone": "3"}) is None
    assert get_charger_wall_clock_seconds(None) is None


def test_charger_wall_clock_rejects_out_of_range_values() -> None:
    assert get_charger_wall_clock_seconds({"systemTime": "-1", "timeZone": "3"}) is None
    assert (
        get_charger_wall_clock_seconds({"systemTime": "99999999999", "timeZone": "3"})
        is None
    )
    now = str(int(time.time()))
    assert get_charger_wall_clock_seconds({"systemTime": now, "timeZone": "15"}) is None
    assert (
        get_charger_wall_clock_seconds({"systemTime": now, "timeZone": "-13"}) is None
    )


# --- get_time_drift ---


def test_time_drift_is_zero_when_in_sync() -> None:
    assert sd.get_time_drift(_updater(0), None) == 0


def test_time_drift_tolerates_small_jitter() -> None:
    assert sd.get_time_drift(_updater(3), None) == 0
    assert sd.get_time_drift(_updater(-3), None) == 0


def test_time_drift_reports_signed_drift() -> None:
    assert sd.get_time_drift(_updater(-3600), None) == -3600
    assert sd.get_time_drift(_updater(3600), None) == 3600


def test_time_drift_quantizes_to_suppress_poll_jitter() -> None:
    # Beyond the tolerance band values snap to a 30 s grid so that a handful of
    # seconds of poll-timing noise never produces a new recorded state.
    assert sd.get_time_drift(_updater(123), None) == 120
    assert sd.get_time_drift(_updater(-123), None) == -120


def test_time_drift_unknown_on_missing_or_corrupt_data() -> None:
    assert sd.get_time_drift(_updater(None), None) is None
    assert sd.get_time_drift(_updater(0, systemTime="bad"), None) is None
    assert sd.get_time_drift(_updater(0, timeZone="bad"), None) is None
    assert sd.get_time_drift(SimpleNamespace(available=True, data=None), None) is None


def test_time_drift_handles_data_access_exception_without_raising() -> None:
    class BrokenUpdater:
        available = True

        @property
        def data(self):
            raise RuntimeError("boom")

    assert sd.get_time_drift(BrokenUpdater(), None) is None


# --- spec wiring: Time Drift replaces System Time ---


def test_time_drift_spec_replaces_system_time() -> None:
    specs = {spec.key: spec for spec in sd.get_sensor_specifications()}
    assert "system_time" not in specs
    drift = specs["time_drift"]
    assert drift.name == "Time Drift"
    assert drift.unit == "s"
    assert drift.value_fn is sd.get_time_drift
    assert not hasattr(sd, "get_system_time")


# --- registry cleanup: the retired System Time entity is pruned on setup ---


class _FakeRegistry:
    def __init__(self) -> None:
        self.removed: list[str] = []

    def async_get_entity_id(self, platform: str, domain: str, unique_id: str) -> str:
        return f"{platform}.{unique_id}"

    def async_remove(self, entity_id: str) -> None:
        self.removed.append(entity_id)


def test_prune_always_removes_retired_entities(monkeypatch) -> None:
    from custom_components import eveus

    reg = _FakeRegistry()
    monkeypatch.setattr(eveus.er, "async_get", lambda hass: reg)
    eveus._prune_unused_entities(object(), 1, eveus.SOC_MODE_ADVANCED, 3)
    assert reg.removed == [
        "sensor.eveus_system_time",
        "switch.eveus_adaptive_mode",
        "number.eveus_minimum_voltage",
        "sensor.eveus_adaptive_voltage_threshold",
    ]


def test_prune_removes_retired_entity_with_device_suffix(monkeypatch) -> None:
    from custom_components import eveus

    reg = _FakeRegistry()
    monkeypatch.setattr(eveus.er, "async_get", lambda hass: reg)
    eveus._prune_unused_entities(object(), 2, eveus.SOC_MODE_ADVANCED, 3)
    assert reg.removed == [
        "sensor.eveus2_system_time",
        "switch.eveus2_adaptive_mode",
        "number.eveus2_minimum_voltage",
        "sensor.eveus2_adaptive_voltage_threshold",
    ]


# --- hysteresis: boundary jitter must not alternate the reported state ---


def _set_offset(updater, offset_seconds: int) -> None:
    updater.data["systemTime"] = str(int(time.time()) + TZ_SHIFT + offset_seconds)


def test_time_drift_boundary_jitter_does_not_flap(monkeypatch) -> None:
    updater = _updater(5)
    assert sd.get_time_drift(updater, None) == 0
    # raw drift oscillating 5 <-> 6 across the tolerance boundary stays 0
    for offset in (6, 5, 6):
        _set_offset(updater, offset)
        assert sd.get_time_drift(updater, None) == 0


def test_time_drift_quantization_boundary_does_not_flap() -> None:
    updater = _updater(44)
    first = sd.get_time_drift(updater, None)
    for offset in (45, 44, 45):
        _set_offset(updater, offset)
        assert sd.get_time_drift(updater, None) == first


def test_time_drift_still_tracks_real_drift_changes() -> None:
    updater = _updater(0)
    assert sd.get_time_drift(updater, None) == 0
    _set_offset(updater, 60)
    assert sd.get_time_drift(updater, None) == 60
    _set_offset(updater, -120)
    assert sd.get_time_drift(updater, None) == -120


def test_time_drift_hysteresis_survives_a_corrupt_poll() -> None:
    updater = _updater(60)
    assert sd.get_time_drift(updater, None) == 60
    updater.data["systemTime"] = "bad"
    assert sd.get_time_drift(updater, None) is None
    _set_offset(updater, 62)
    assert sd.get_time_drift(updater, None) == 60


# --- wall-clock awareness: wrong Time Zone select / DST mismatch is visible ---


def test_time_drift_detects_wrong_timezone_select() -> None:
    # Charger clock synced (UTC correct) but its Time Zone select says +2
    # while HA runs +3: the charger's wall clock is an hour behind, schedules
    # would mistime, and the sensor must say so.
    now = int(time.time())
    updater = SimpleNamespace(
        available=True,
        data={"systemTime": str(now + 2 * 3600), "timeZone": "2"},
    )
    assert sd.get_time_drift(updater, None) == -3600


def test_time_drift_detects_dst_mismatch() -> None:
    # After a DST change HA moves to +2 (winter) while the charger's fixed
    # offset stays +3: charger wall clock is now an hour ahead.
    dt_util.set_default_time_zone(timezone(timedelta(hours=2)))
    assert sd.get_time_drift(_updater(0), None) == 3600


# --- repair classification: wrong timezone vs clock needs syncing ---


def _drift_payload(drift_seconds: int) -> dict:
    return {
        "systemTime": int(time.time()) + TZ_SHIFT + drift_seconds,
        "timeZone": str(TZ_HOURS),
    }


def _fired_tracker(drift_seconds: int):
    from custom_components.eveus import _ClockDriftTracker

    tracker = _ClockDriftTracker()
    decision = None
    for _ in range(3):
        decision = tracker.evaluate(_drift_payload(drift_seconds))
    assert decision is True
    return tracker


def test_tracker_missing_time_fields_reset_rekey_state() -> None:
    # A successful poll that omits the time fields can't classify drift; it must
    # not advance the re-key streak on stale state or leave `still_drifted` set,
    # or two such polls could re-publish a stale clock-drift message.
    from custom_components.eveus import _ClockDriftTracker

    tracker = _ClockDriftTracker()
    tracker.still_drifted = True
    tracker.rekey_streak = 2

    assert tracker.evaluate({}) is None
    assert tracker.still_drifted is False
    assert tracker.rekey_streak == 0


def test_tracker_classifies_whole_hour_drift_as_timezone() -> None:
    assert _fired_tracker(-3600).kind == "timezone"
    assert _fired_tracker(3600).kind == "timezone"
    # RTC slightly off on top of a wrong-by-two-hours timezone still counts
    assert _fired_tracker(-7180).kind == "timezone"


def test_tracker_classifies_other_drift_as_sync() -> None:
    assert _fired_tracker(900).kind == "sync"
    assert _fired_tracker(-1800).kind == "sync"


def test_tracker_reports_hours_for_timezone_kind() -> None:
    assert _fired_tracker(-3600).hours == 1
    assert _fired_tracker(7220).hours == 2


def test_clock_drift_issue_uses_kind_specific_translation_key(monkeypatch) -> None:
    from types import SimpleNamespace as NS

    from custom_components import eveus

    created: list[dict] = []
    monkeypatch.setattr(
        eveus.ir,
        "async_create_issue",
        lambda hass, domain, issue_id, **kw: created.append(kw),
    )
    entry = NS(entry_id="e1")

    for drift, key in ((-3600, "clock_drift_timezone"), (900, "clock_drift")):
        tracker = eveus._ClockDriftTracker()
        updater = NS(available=True, last_update_success=True, data=None)
        for _ in range(3):
            updater.data = _drift_payload(drift)
            eveus._update_clock_drift_issue(object(), entry, updater, tracker)
        assert created[-1]["translation_key"] == key
    assert created[0]["translation_placeholders"] == {"hours": "1"}


def test_timezone_repair_text_exists_in_all_locales() -> None:
    import json
    from pathlib import Path

    base = Path("custom_components/eveus")
    for name in ("strings.json", "translations/en.json", "translations/uk.json"):
        issues = json.loads((base / name).read_text())["issues"]
        desc = issues["clock_drift_timezone"]["description"]
        assert "Time Zone" in desc or "Часовий пояс" in desc, name
        assert "{hours}" in desc, name
