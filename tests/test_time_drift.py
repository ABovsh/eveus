"""Tests for the Time Drift diagnostic sensor replacing System Time."""
from __future__ import annotations

import time
from types import SimpleNamespace

import custom_components.eveus.sensor_definitions as sd
from custom_components.eveus.utils import get_charger_utc_seconds

TZ_HOURS = 3
TZ_SHIFT = TZ_HOURS * 3600


def _updater(offset_seconds: int | None = None, **overrides):
    """Updater whose charger clock is `offset_seconds` away from real time."""
    data: dict[str, object] = {"timeZone": str(TZ_HOURS)}
    if offset_seconds is not None:
        data["systemTime"] = str(int(time.time()) + TZ_SHIFT + offset_seconds)
    data.update(overrides)
    return SimpleNamespace(available=True, data=data)


# --- get_charger_utc_seconds (shared helper) ---


def test_charger_utc_decodes_timezone_shift() -> None:
    now = int(time.time())
    data = {"systemTime": str(now + TZ_SHIFT), "timeZone": str(TZ_HOURS)}
    assert abs(get_charger_utc_seconds(data) - now) <= 1


def test_charger_utc_requires_both_fields() -> None:
    now = int(time.time())
    assert get_charger_utc_seconds({"systemTime": str(now)}) is None
    assert get_charger_utc_seconds({"timeZone": "3"}) is None
    assert get_charger_utc_seconds(None) is None


def test_charger_utc_rejects_out_of_range_values() -> None:
    assert get_charger_utc_seconds({"systemTime": "-1", "timeZone": "3"}) is None
    assert (
        get_charger_utc_seconds({"systemTime": "99999999999", "timeZone": "3"}) is None
    )
    now = str(int(time.time()))
    assert get_charger_utc_seconds({"systemTime": now, "timeZone": "15"}) is None
    assert get_charger_utc_seconds({"systemTime": now, "timeZone": "-13"}) is None


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
    # Beyond the tolerance band values snap to a 10 s grid so that ±1-2 s of
    # poll-timing noise never produces a new recorded state.
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


def test_prune_always_removes_retired_system_time_entity(monkeypatch) -> None:
    from custom_components import eveus

    reg = _FakeRegistry()
    monkeypatch.setattr(eveus.er, "async_get", lambda hass: reg)
    eveus._prune_unused_entities(object(), 1, eveus.SOC_MODE_ADVANCED, 3)
    assert reg.removed == ["sensor.eveus_system_time"]


def test_prune_removes_retired_entity_with_device_suffix(monkeypatch) -> None:
    from custom_components import eveus

    reg = _FakeRegistry()
    monkeypatch.setattr(eveus.er, "async_get", lambda hass: reg)
    eveus._prune_unused_entities(object(), 2, eveus.SOC_MODE_ADVANCED, 3)
    assert reg.removed == ["sensor.eveus2_system_time"]
