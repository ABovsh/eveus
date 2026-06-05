"""Tests for the low RTC backup battery (CR2032 / vBat) repair warning."""
from __future__ import annotations

import pytest

from conftest import EveusTestUpdater as _Updater
import custom_components.eveus as eveus_init
from custom_components.eveus import (
    _BatteryLowTracker,
    _battery_low_issue_id,
    _update_battery_low_issue,
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
    t = _BatteryLowTracker()
    low = BATTERY_LOW_THRESHOLD_VOLTS - 0.1
    # First (debounce - 1) low readings must NOT fire.
    for _ in range(BATTERY_LOW_DEBOUNCE_POLLS - 1):
        assert t.evaluate(low) is None
    # The Nth consecutive low reading fires exactly once.
    assert t.evaluate(low) is True
    # Staying low keeps it active without re-firing.
    assert t.evaluate(low) is None


def test_tracker_clears_only_above_ok_threshold() -> None:
    t = _BatteryLowTracker()
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
    t = _BatteryLowTracker()
    low = BATTERY_LOW_THRESHOLD_VOLTS - 0.1
    # Low readings interrupted by a healthy one must restart the debounce count.
    for _ in range(BATTERY_LOW_DEBOUNCE_POLLS - 1):
        t.evaluate(low)
    assert t.evaluate(BATTERY_OK_THRESHOLD_VOLTS + 0.5) is None  # streak reset
    for _ in range(BATTERY_LOW_DEBOUNCE_POLLS - 1):
        assert t.evaluate(low) is None
    assert t.evaluate(low) is True


def test_tracker_ignores_invalid_readings() -> None:
    t = _BatteryLowTracker()
    low = BATTERY_LOW_THRESHOLD_VOLTS - 0.1
    # Build up part of the streak.
    for _ in range(BATTERY_LOW_DEBOUNCE_POLLS - 1):
        t.evaluate(low)
    # None / 0 (offline or garbled) are not "low" — they neither fire nor reset.
    assert t.evaluate(None) is None
    assert t.evaluate(0.0) is None
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
    tracker = _BatteryLowTracker()
    low = BATTERY_LOW_THRESHOLD_VOLTS - 0.1

    for _ in range(BATTERY_LOW_DEBOUNCE_POLLS - 1):
        _update_battery_low_issue(object(), entry, _Updater({"vBat": low}), tracker)
    assert not created
    _update_battery_low_issue(object(), entry, _Updater({"vBat": low}), tracker)

    assert created and created[0]["issue_id"] == _battery_low_issue_id(entry)
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
    tracker = _BatteryLowTracker()
    low = BATTERY_LOW_THRESHOLD_VOLTS - 0.1
    for _ in range(BATTERY_LOW_DEBOUNCE_POLLS):
        _update_battery_low_issue(object(), entry, _Updater({"vBat": low}), tracker)
    assert created == [_battery_low_issue_id(entry)]

    _update_battery_low_issue(
        object(), entry, _Updater({"vBat": BATTERY_OK_THRESHOLD_VOLTS + 0.3}), tracker
    )
    assert deleted == [_battery_low_issue_id(entry)]


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
    tracker = _BatteryLowTracker()
    for payload in ({}, {"vBat": None}, {"vBat": "bad"}, {"vBat": 0}):
        _update_battery_low_issue(object(), entry, _Updater(payload), tracker)
    assert not created
    assert not deleted
