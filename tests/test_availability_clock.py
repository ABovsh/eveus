"""Availability / clock / monotonic tests.

Tests for backward clock handling, grace re-anchor, optimistic-value expiry
on clock step, offline detection immune to wall-clock, seconds_since_success,
and ratelog monotonic behaviour.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from conftest import EveusTestUpdater, disable_state_writes


def test_command_rate_limit_wait_is_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    from custom_components.eveus import common_command

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(common_command.asyncio, "sleep", fake_sleep)

    manager = common_command.CommandManager(SimpleNamespace())

    async def fake_post(command, value, extra=None):
        return True

    manager._post_command = fake_post
    # Simulate the wall clock having jumped backward by an hour after the last
    # command: time_since_last becomes strongly negative.
    manager._last_command_time = time.time() + 3600

    assert asyncio.run(manager.send_command("currentSet", 16)) is True
    # Without the clamp this would be ~3601s; with it, never more than 1s.
    assert all(delay <= 1.0 for delay in sleeps)


def test_optimistic_value_expires_when_clock_steps_backward() -> None:
    from custom_components.eveus.common_base import OptimisticControlMixin

    ctrl = OptimisticControlMixin()
    ctrl._init_optimistic_control()
    ctrl._set_optimistic_value(7)
    stamp = ctrl._optimistic_value_time

    assert ctrl._optimistic_value_is_valid(stamp - 50, 120) is False
    ctrl._expire_optimistic_value(stamp - 50, 120)
    assert ctrl._optimistic_value is None


def test_optimistic_value_valid_within_ttl() -> None:
    from custom_components.eveus.common_base import OptimisticControlMixin

    ctrl = OptimisticControlMixin()
    ctrl._init_optimistic_control()
    ctrl._set_optimistic_value(7)
    stamp = ctrl._optimistic_value_time
    assert ctrl._optimistic_value_is_valid(stamp + 5, 120) is True


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


def test_availability_grace_uses_monotonic_not_wall_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for A04-adjacent A03 finding: the grace window is timed
    with time.monotonic(), so a wall-clock jump (NTP correction, DST, manual
    change) in either direction cannot move the outage boundary or reopen /
    prematurely expire the grace window.
    """
    from custom_components.eveus import common_base

    updater = EveusTestUpdater({}, available=False)
    sensor = _diag_sensor(updater)

    fake_monotonic = 1_000_000.0
    monkeypatch.setattr(common_base.time, "monotonic", lambda: fake_monotonic)
    # Wall clock jumps wildly forward; must have zero effect on grace timing.
    monkeypatch.setattr(common_base.time, "time", lambda: 4_102_444_800.0)

    sensor._update_availability_state()
    assert sensor._unavailable_since == fake_monotonic
    assert sensor.available is True

    # Still within the grace period per monotonic time.
    fake_monotonic += 1
    sensor._update_availability_state()
    assert sensor.available is True

    # Grace period genuinely expires once enough monotonic time has passed.
    fake_monotonic += 10_000
    sensor._update_availability_state()
    assert sensor.available is False


def test_control_fallback_rejects_future_read_timestamp() -> None:
    from custom_components.eveus.number import EveusCurrentNumber

    entity = EveusCurrentNumber(EveusTestUpdater({}, available=False), "16A")
    entity._last_device_value = 10.0
    entity._last_successful_read = time.time() + 10_000  # backward jump

    assert entity._resolve_value() is None


def test_o03_ratelog_uses_monotonic_clock(monkeypatch):
    import custom_components.eveus.utils as u
    from custom_components.eveus.utils import RateLog

    clock = {"m": 1000.0}
    monkeypatch.setattr(u.time, "monotonic", lambda: clock["m"])
    rl = RateLog()
    assert rl.should_log(10) is True       # first emission
    clock["m"] = 1005.0
    assert rl.should_log(10) is False      # within interval
    clock["m"] = 1011.0
    assert rl.should_log(10) is True       # interval elapsed (monotonic)


def test_o03_ratelog_first_log_emits_even_with_small_monotonic(monkeypatch):
    # Regression for the CI env gap: right after boot monotonic() is small, so a
    # 0.0 sentinel would suppress the first log when interval > uptime.
    import custom_components.eveus.utils as u
    from custom_components.eveus.utils import RateLog

    monkeypatch.setattr(u.time, "monotonic", lambda: 0.5)  # tiny uptime
    rl = RateLog()
    assert rl.should_log(300) is True            # first call must still log
    assert rl.should_log(300, key="x") is True   # first keyed call too


def test_invalid_state_keeps_offline_cadence() -> None:
    from datetime import timedelta
    from custom_components.eveus import common_network
    from custom_components.eveus.common_network import EveusUpdater
    from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, object())
    updater._tune_update_interval({"state": 99})
    assert updater.update_interval == timedelta(seconds=common_network.OFFLINE_UPDATE_INTERVAL)


def test_known_state_picks_idle_cadence() -> None:
    from datetime import timedelta
    from custom_components.eveus import common_network
    from custom_components.eveus.common_network import EveusUpdater
    from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, object())
    updater._tune_update_interval({"state": 3})  # Connected
    assert updater.update_interval == timedelta(seconds=common_network.IDLE_UPDATE_INTERVAL)


def test_state_transition_triggers_burst(monkeypatch) -> None:
    from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
    from custom_components.eveus.common_network import EveusUpdater

    class _Hass:
        loop = None

    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    calls = []
    monkeypatch.setattr(updater, "_schedule_post_command_refresh", lambda: calls.append(1))
    updater._record_success(0.1, {"state": 2})
    updater._record_success(0.1, {"state": 4})
    assert len(calls) == 1


def test_unchanged_state_does_not_burst(monkeypatch) -> None:
    from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
    from custom_components.eveus.common_network import EveusUpdater

    class _Hass:
        loop = None

    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    calls = []
    monkeypatch.setattr(updater, "_schedule_post_command_refresh", lambda: calls.append(1))
    updater._record_success(0.1, {"state": 4})
    updater._record_success(0.1, {"state": 4})
    assert calls == []


def test_first_poll_does_not_burst(monkeypatch) -> None:
    from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
    from custom_components.eveus.common_network import EveusUpdater

    class _Hass:
        loop = None

    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    calls = []
    monkeypatch.setattr(updater, "_schedule_post_command_refresh", lambda: calls.append(1))
    updater._record_success(0.1, {"state": 4})
    assert calls == []


def test_invalid_state_neither_bursts_nor_clears_memory(monkeypatch) -> None:
    from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
    from custom_components.eveus.common_network import EveusUpdater

    class _Hass:
        loop = None

    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    calls = []
    monkeypatch.setattr(updater, "_schedule_post_command_refresh", lambda: calls.append(1))
    updater._record_success(0.1, {"state": 2})
    updater._record_success(0.1, {})  # validator normally rejects; be safe
    updater._record_success(0.1, {"state": 2})
    assert calls == []


def test_offline_poll_cycle_is_at_most_sixty_seconds():
    from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
    from custom_components.eveus.common_network import EveusUpdater
    from custom_components.eveus.const import OFFLINE_UPDATE_INTERVAL

    class _Hass:
        loop = None

    def _make_offline(updater):
        import time as _t
        updater._consecutive_failures = 11
        updater._last_success_monotonic = _t.monotonic() - 700
        updater._last_success_time = _t.time() - 700

    assert OFFLINE_UPDATE_INTERVAL <= 60
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    _make_offline(updater)
    for _ in range(6):
        updater._record_failure(TimeoutError())
        delay = updater._next_poll_attempt - time.time()
        assert delay <= OFFLINE_UPDATE_INTERVAL
    assert updater.update_interval.total_seconds() == OFFLINE_UPDATE_INTERVAL


def test_backoff_does_not_escalate_with_continued_failures():
    from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
    from custom_components.eveus.common_network import EveusUpdater

    class _Hass:
        loop = None

    def _make_offline(updater):
        import time as _t
        updater._consecutive_failures = 11
        updater._last_success_monotonic = _t.monotonic() - 700
        updater._last_success_time = _t.time() - 700

    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    _make_offline(updater)
    updater._record_failure(TimeoutError())
    first = updater._next_poll_attempt - time.time()
    for _ in range(5):
        updater._record_failure(TimeoutError())
    assert updater._next_poll_attempt - time.time() == pytest.approx(first, abs=1.0)


def test_recovery_needs_two_successes_before_fast_cadence():
    from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
    from custom_components.eveus.common_network import EveusUpdater
    from custom_components.eveus.const import CHARGING_UPDATE_INTERVAL, OFFLINE_UPDATE_INTERVAL

    class _Hass:
        loop = None

    def _make_offline(updater):
        import time as _t
        updater._consecutive_failures = 11
        updater._last_success_monotonic = _t.monotonic() - 700
        updater._last_success_time = _t.time() - 700

    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    _make_offline(updater)
    updater._record_failure(TimeoutError())
    updater._record_success(0.1, {"state": 4})
    assert updater.update_interval.total_seconds() == OFFLINE_UPDATE_INTERVAL
    updater._record_success(0.1, {"state": 4})
    assert updater.update_interval.total_seconds() == OFFLINE_UPDATE_INTERVAL
    updater._record_success(0.1, {"state": 4})
    assert updater.update_interval.total_seconds() == CHARGING_UPDATE_INTERVAL


def test_fires_after_three_polls_above_ten_minutes() -> None:
    from custom_components.eveus import _ClockDriftTracker

    def _p(drift: float, tz: int = 3) -> dict:
        return {"systemTime": int(time.time() + drift + tz * 3600), "timeZone": tz}

    tracker = _ClockDriftTracker()
    assert tracker.evaluate(_p(900)) is None
    assert tracker.evaluate(_p(900)) is None
    assert tracker.evaluate(_p(900)) is True


def test_failure_during_probation_resets_counter() -> None:
    from types import SimpleNamespace
    from custom_components.eveus.common_network import EveusUpdater

    updater = EveusUpdater.__new__(EveusUpdater)
    updater._connection_quality_cache = None
    updater._poll_results = []
    updater._consecutive_failures = 0
    updater._device_available = True
    updater._last_error = None
    updater._silent_mode = False
    updater._offline_announced = False
    updater._next_poll_attempt = 0.0
    updater._last_success_time = 0.0
    updater._last_success_monotonic = 0.0
    updater._availability_log = SimpleNamespace(should_log=lambda *_: False)
    updater._offline_probation = 1

    updater._record_failure(ValueError("boom"))
    assert updater._offline_probation == 2


@pytest.fixture
def _ha_local_clock_utc_plus_3_avail():
    from datetime import timedelta, timezone as _tz
    from homeassistant.util import dt as dt_util

    original = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(_tz(timedelta(hours=3)))
    yield
    dt_util.set_default_time_zone(original)


def test_clock_drift_does_not_clear_while_still_minutes_wrong(_ha_local_clock_utc_plus_3_avail) -> None:
    from custom_components.eveus import _ClockDriftTracker

    def _payload(drift: float, tz: int = 3) -> dict:
        return {"systemTime": int(time.time() + drift + tz * 3600), "timeZone": tz}

    tracker = _ClockDriftTracker()
    for _ in range(2):
        tracker.evaluate(_payload(900))
    assert tracker.evaluate(_payload(900)) is True
    # Hovering just under the trigger threshold: never clears.
    for _ in range(5):
        assert tracker.evaluate(_payload(590)) is None
    # Truly back in sync: clears after the configured streak.
    assert tracker.evaluate(_payload(10)) is None
    assert tracker.evaluate(_payload(10)) is False


def test_clock_drift_hover_then_resync_needs_consecutive_in_sync_polls(_ha_local_clock_utc_plus_3_avail) -> None:
    from custom_components.eveus import _ClockDriftTracker

    def _payload(drift: float, tz: int = 3) -> dict:
        return {"systemTime": int(time.time() + drift + tz * 3600), "timeZone": tz}

    tracker = _ClockDriftTracker()
    for _ in range(3):
        tracker.evaluate(_payload(900))
    assert tracker.evaluate(_payload(10)) is None
    assert tracker.evaluate(_payload(500)) is None  # band: resets the streak
    assert tracker.evaluate(_payload(10)) is None
    assert tracker.evaluate(_payload(10)) is False


def test_flapping_state_is_debounced(monkeypatch) -> None:
    from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
    from custom_components.eveus.common_network import EveusUpdater

    class _Hass:
        loop = None

    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    calls = []
    monkeypatch.setattr(updater, "_schedule_post_command_refresh", lambda: calls.append(1))
    updater._record_success(0.1, {"state": 2})
    updater._record_success(0.1, {"state": 4})
    updater._record_success(0.1, {"state": 2})
    updater._record_success(0.1, {"state": 4})
    assert len(calls) == 1


def test_single_blip_does_not_enter_probation():
    from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
    from custom_components.eveus.common_network import EveusUpdater
    from custom_components.eveus.const import CHARGING_UPDATE_INTERVAL

    class _Hass:
        loop = None

    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._record_success(0.1, {"state": 4})
    updater._record_failure(TimeoutError())
    updater._record_success(0.1, {"state": 4})
    assert updater.update_interval.total_seconds() == CHARGING_UPDATE_INTERVAL


def test_negative_drift_also_fires(_ha_local_clock_utc_plus_3_avail) -> None:
    from custom_components.eveus import _ClockDriftTracker

    def _p(drift: float, tz: int = 3) -> dict:
        return {"systemTime": int(time.time() + drift + tz * 3600), "timeZone": tz}

    tracker = _ClockDriftTracker()
    for _ in range(2):
        tracker.evaluate(_p(-900))
    assert tracker.evaluate(_p(-900)) is True


def test_small_drift_never_fires_and_clears_after_two_polls(_ha_local_clock_utc_plus_3_avail) -> None:
    from custom_components.eveus import _ClockDriftTracker

    def _p(drift: float, tz: int = 3) -> dict:
        return {"systemTime": int(time.time() + drift + tz * 3600), "timeZone": tz}

    tracker = _ClockDriftTracker()
    for _ in range(3):
        tracker.evaluate(_p(900))
    assert tracker.evaluate(_p(30)) is None
    assert tracker.evaluate(_p(30)) is False


def test_one_in_sync_poll_resets_debounce(_ha_local_clock_utc_plus_3_avail) -> None:
    from custom_components.eveus import _ClockDriftTracker

    def _p(drift: float, tz: int = 3) -> dict:
        return {"systemTime": int(time.time() + drift + tz * 3600), "timeZone": tz}

    tracker = _ClockDriftTracker()
    tracker.evaluate(_p(900))
    tracker.evaluate(_p(900))
    tracker.evaluate(_p(0))
    assert tracker.evaluate(_p(900)) is None
