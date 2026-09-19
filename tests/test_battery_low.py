"""Tests for the low RTC backup battery (CR2032 / vBat) repair warning."""
from __future__ import annotations

import pytest

from conftest import PayloadUpdater
from conftest import EveusTestUpdater as _Updater
import custom_components.eveus as eveus_init
from custom_components.eveus import (
    BatteryLowTracker,
    battery_low_issue_id,
    update_battery_low_issue,
)
from custom_components.eveus.const import (
    BATTERY_LOW_DEBOUNCE_POLLS,
    BATTERY_LOW_THRESHOLD_VOLTS,
    BATTERY_OK_THRESHOLD_VOLTS,
)


def test_thresholds_have_hysteresis_gap() -> None:
    # Clear threshold must sit above the fire threshold or the issue would flap.
    assert BATTERY_OK_THRESHOLD_VOLTS > BATTERY_LOW_THRESHOLD_VOLTS
    assert BATTERY_LOW_DEBOUNCE_POLLS >= 2


def test_tracker_fires_only_after_debounce() -> None:
    t = BatteryLowTracker()
    low = BATTERY_LOW_THRESHOLD_VOLTS - 0.1
    # First (debounce - 1) low readings must NOT fire.
    for _ in range(BATTERY_LOW_DEBOUNCE_POLLS - 1):
        assert t.evaluate(low) is None
    # The Nth consecutive low reading fires exactly once.
    assert t.evaluate(low) is True
    # Staying low keeps it active without re-firing.
    assert t.evaluate(low) is None


def test_tracker_clears_only_above_ok_threshold() -> None:
    t = BatteryLowTracker()
    low = BATTERY_LOW_THRESHOLD_VOLTS - 0.1
    for _ in range(BATTERY_LOW_DEBOUNCE_POLLS):
        t.evaluate(low)
    # A reading in the dead band (>= fire, < clear) must NOT clear the warning.
    dead_band = (BATTERY_LOW_THRESHOLD_VOLTS + BATTERY_OK_THRESHOLD_VOLTS) / 2
    assert t.evaluate(dead_band) is None
    # Only a reading at/above the clear threshold clears it, once.
    assert t.evaluate(BATTERY_OK_THRESHOLD_VOLTS) is False
    assert t.evaluate(BATTERY_OK_THRESHOLD_VOLTS) is None


def test_tracker_resets_streak_on_recovery() -> None:
    t = BatteryLowTracker()
    low = BATTERY_LOW_THRESHOLD_VOLTS - 0.1
    # Low readings interrupted by a healthy one must restart the debounce count.
    for _ in range(BATTERY_LOW_DEBOUNCE_POLLS - 1):
        t.evaluate(low)
    assert t.evaluate(BATTERY_OK_THRESHOLD_VOLTS + 0.5) is None  # streak reset
    for _ in range(BATTERY_LOW_DEBOUNCE_POLLS - 1):
        assert t.evaluate(low) is None
    assert t.evaluate(low) is True


def test_tracker_ignores_invalid_readings() -> None:
    """An unusable reading is not "low": it neither fires nor resets.

    The tracker sees `None` for both cases now — offline, and a `vBat` the
    shared parse rejected (0 V, or above the coin cell's plausible ceiling);
    test_battery_notice_reads_the_updater_snapshot covers the parse side.
    """
    t = BatteryLowTracker()
    low = BATTERY_LOW_THRESHOLD_VOLTS - 0.1
    # Build up part of the streak.
    for _ in range(BATTERY_LOW_DEBOUNCE_POLLS - 1):
        t.evaluate(low)
    assert t.evaluate(None) is None
    assert t.evaluate(None) is None
    # The next genuine low reading completes the original streak.
    assert t.evaluate(low) is True


def test_update_creates_issue_after_debounce(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, object]] = []
    deleted: list[str] = []
    monkeypatch.setattr(
        eveus_init.ir,
        "async_create_issue",
        lambda hass, domain, issue_id, **kw: created.append({"issue_id": issue_id, **kw}),
    )
    monkeypatch.setattr(
        eveus_init.ir,
        "async_delete_issue",
        lambda hass, domain, issue_id: deleted.append(issue_id),
    )
    entry = type("E", (), {"entry_id": "abc"})()
    tracker = BatteryLowTracker()
    low = BATTERY_LOW_THRESHOLD_VOLTS - 0.1

    for _ in range(BATTERY_LOW_DEBOUNCE_POLLS - 1):
        update_battery_low_issue(object(), entry, _Updater({"vBat": low}), tracker)
    assert not created
    update_battery_low_issue(object(), entry, _Updater({"vBat": low}), tracker)

    assert created
    assert created[0]["issue_id"] == battery_low_issue_id(entry)
    assert created[0]["translation_key"] == "battery_low"
    assert created[0]["is_fixable"] is False
    assert created[0]["severity"] is eveus_init.ir.IssueSeverity.WARNING
    assert not deleted


def test_update_clears_issue_on_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[str] = []
    deleted: list[str] = []
    monkeypatch.setattr(
        eveus_init.ir,
        "async_create_issue",
        lambda hass, domain, issue_id, **kw: created.append(issue_id),
    )
    monkeypatch.setattr(
        eveus_init.ir,
        "async_delete_issue",
        lambda hass, domain, issue_id: deleted.append(issue_id),
    )
    entry = type("E", (), {"entry_id": "abc"})()
    tracker = BatteryLowTracker()
    low = BATTERY_LOW_THRESHOLD_VOLTS - 0.1
    for _ in range(BATTERY_LOW_DEBOUNCE_POLLS):
        update_battery_low_issue(object(), entry, _Updater({"vBat": low}), tracker)
    assert created == [battery_low_issue_id(entry)]

    update_battery_low_issue(
        object(), entry, _Updater({"vBat": BATTERY_OK_THRESHOLD_VOLTS + 0.3}), tracker
    )
    assert deleted == [battery_low_issue_id(entry)]


def test_update_does_not_fire_on_missing_field(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[str] = []
    deleted: list[str] = []
    monkeypatch.setattr(
        eveus_init.ir,
        "async_create_issue",
        lambda hass, domain, issue_id, **kw: created.append(issue_id),
    )
    monkeypatch.setattr(
        eveus_init.ir,
        "async_delete_issue",
        lambda hass, domain, issue_id: deleted.append(issue_id),
    )
    entry = type("E", (), {"entry_id": "abc"})()
    tracker = BatteryLowTracker()
    for payload in ({}, {"vBat": None}, {"vBat": "bad"}, {"vBat": 0}):
        update_battery_low_issue(object(), entry, _Updater(payload), tracker)
    assert not created
    assert not deleted


from types import SimpleNamespace


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

    tracker = BatteryLowTracker()
    entry = SimpleNamespace(entry_id="e1")
    low = BATTERY_LOW_THRESHOLD_VOLTS - 0.5

    # One genuine low reading.
    updater = _Updater({"vBat": low})
    update_battery_low_issue(None, entry, updater, tracker)

    # The charger drops offline; the coordinator keeps notifying listeners
    # with the stale payload. The debounce must not advance.
    updater.available = False
    updater.last_update_success = False
    for _ in range(10):
        update_battery_low_issue(None, entry, updater, tracker)

    assert recorder.created == []
    assert tracker._low_streak == 1


def test_corrupt_high_vbat_does_not_clear_active_warning() -> None:
    tracker = BatteryLowTracker()
    low = BATTERY_LOW_THRESHOLD_VOLTS - 0.5
    decisions = [tracker.evaluate(low) for _ in range(3)]
    assert decisions[-1] is True  # warning raised

    # An implausible finite spike reaches the tracker as None (the shared parse
    # dropped it) and must neither clear nor restart anything.
    from custom_components.eveus.const import BATTERY_VBAT_MAX_PLAUSIBLE_VOLTS
    from custom_components.eveus.snapshot import EveusSnapshot

    spike = EveusSnapshot.parse(
        {"vBat": BATTERY_VBAT_MAX_PLAUSIBLE_VOLTS + 100.0}, None
    ).get("vBat")
    assert spike is None
    assert tracker.evaluate(spike) is None
    assert tracker._active is True


import pytest


@pytest.mark.parametrize("bad", [0, 5.01, 12.5, 100, 500])
def test_v09_battery_voltage_rejects_implausible(bad):
    upd = PayloadUpdater({"vBat": bad})
    from custom_components.eveus import sensor_definitions as sd
    assert sd.get_battery_voltage(upd, None) is None


def test_v09_battery_voltage_accepts_plausible():
    upd = PayloadUpdater({"vBat": 3.0})
    from custom_components.eveus import sensor_definitions as sd
    assert sd.get_battery_voltage(upd, None) == 3.0


def test_battery_voltage_rejects_negative() -> None:
    from custom_components.eveus import sensor_definitions as sd

    assert sd.get_battery_voltage(_Updater({"vBat": -2.5}), None) is None


# --- Snapshot migration (P2.2) ---------------------------------------------


def test_battery_notice_reads_the_updater_snapshot(monkeypatch) -> None:
    """The coin-cell plausibility window is applied once, in the shared parse:
    the tracker is handed a value that is already usable or None."""
    from types import SimpleNamespace as _NS

    from custom_components.eveus.const import BATTERY_VBAT_MAX_PLAUSIBLE_VOLTS
    from custom_components.eveus.snapshot import EveusSnapshot

    created: list[str] = []
    monkeypatch.setattr(
        eveus_init.ir,
        "async_create_issue",
        lambda hass, domain, issue_id, **kw: created.append(issue_id),
    )
    monkeypatch.setattr(
        eveus_init.ir, "async_delete_issue", lambda hass, domain, issue_id: None
    )
    entry = type("E", (), {"entry_id": "abc"})()
    tracker = BatteryLowTracker()

    def _poll(vbat):
        updater = _NS(
            available=True,
            last_update_success=True,
            data=None,  # only the snapshot carries the reading
            snapshot=EveusSnapshot.parse({"state": 2, "vBat": vbat}, None),
        )
        update_battery_low_issue(object(), entry, updater, tracker)

    low = BATTERY_LOW_THRESHOLD_VOLTS - 0.1
    for _ in range(BATTERY_LOW_DEBOUNCE_POLLS):
        _poll(low)
    assert created == [battery_low_issue_id(entry)]

    # A corrupt spike above the plausible window is filtered by the snapshot,
    # so it can neither clear the warning nor restart the debounce.
    _poll(BATTERY_VBAT_MAX_PLAUSIBLE_VOLTS + 100.0)
    _poll(0)
    assert tracker._active is True
