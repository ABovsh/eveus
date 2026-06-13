"""Tests for the 4.13 feature round: device firmware metadata, target-SOC
energy/cost sensors, transition burst polling, progressive offline backoff,
and the charger clock-drift notice."""
from __future__ import annotations

import conftest  # noqa: F401  (installs HA stubs)

from custom_components.eveus import utils
from conftest import TEST_HOST


class TestDeviceFirmwareMetadata:
    """Wi-Fi firmware must not masquerade as the charger hardware revision."""

    def test_device_info_omits_hw_version(self):
        info = utils.get_device_info(
            TEST_HOST,
            {"verFWMain": "GRM070A-R3.05.2", "verFWWifi": "1PGRW001A-R3.05.2"},
        )
        assert "hw_version" not in info
        # Both firmware strings are folded into sw_version, app board (verFWWifi)
        # leading; neither is exposed as a hardware revision.
        assert info["sw_version"] == "1PGRW001A-R3.05.2 (GRM070A-R3.05.2)"

    def test_device_info_omits_hw_version_even_with_legacy_hardware_key(self):
        info = utils.get_device_info(TEST_HOST, {"verFWMain": "x1", "hardware": "h1"})
        assert "hw_version" not in info


# =============================================================================
# #15 Energy & Cost to Target SOC (Advanced mode only)
# =============================================================================

import pytest


@pytest.fixture(autouse=True)
def _ha_local_clock_utc_plus_3():
    """Clock-drift maths compare wall clocks; pin HA's local offset to +3."""
    from datetime import timedelta, timezone as _tz
    from homeassistant.util import dt as dt_util

    original = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(_tz(timedelta(hours=3)))
    yield
    dt_util.set_default_time_zone(original)

from conftest import EV_HELPERS, EveusTestUpdater
from custom_components.eveus.ev_sensors import (
    CachedSOCCalculator,
    CostToTargetSocSensor,
    EnergyToTargetSocSensor,
)


def _push_helpers(calculator: CachedSOCCalculator) -> CachedSOCCalculator:
    for entity_id, value in EV_HELPERS.items():
        calculator.set_value(entity_id.removeprefix("input_number.ev_"), float(value))
    return calculator


class TestEnergyToTargetSoc:
    def test_reports_grid_energy_needed_to_reach_target(self):
        # initial 20% of 80 kWh = 16 kWh; +16 kWh session at 10% loss = 30.4 kWh
        # = 38% SOC. Remaining to 80%: 33.6 kWh battery -> 37.33 kWh from grid.
        calc = _push_helpers(CachedSOCCalculator())
        sensor = EnergyToTargetSocSensor(
            EveusTestUpdater({"sessionEnergy": "16"}), 1, calc
        )
        assert sensor._get_sensor_value() == pytest.approx(37.33, abs=0.01)

    def test_reports_zero_when_target_reached(self):
        calc = _push_helpers(CachedSOCCalculator())
        calc.set_value("target_soc", 20.0)
        sensor = EnergyToTargetSocSensor(EveusTestUpdater({"sessionEnergy": "0"}), 1, calc)
        assert sensor._get_sensor_value() == 0.0

    def test_unknown_without_target_soc(self):
        calc = _push_helpers(CachedSOCCalculator())
        calc.set_value("target_soc", None)
        sensor = EnergyToTargetSocSensor(EveusTestUpdater({"sessionEnergy": "16"}), 1, calc)
        assert sensor._get_sensor_value() is None

    def test_unknown_when_session_energy_corrupt(self):
        calc = _push_helpers(CachedSOCCalculator())
        sensor = EnergyToTargetSocSensor(
            EveusTestUpdater({"sessionEnergy": "-5"}), 1, calc
        )
        assert sensor._get_sensor_value() is None


class TestCostToTargetSoc:
    def test_prices_remaining_energy_with_active_tariff(self):
        # 37.33 kWh from grid at tarif=432 hundredths -> 4.32 UAH/kWh.
        calc = _push_helpers(CachedSOCCalculator())
        sensor = CostToTargetSocSensor(
            EveusTestUpdater({"sessionEnergy": "16", "activeTarif": 0, "tarif": 432}),
            1,
            calc,
        )
        assert sensor._get_sensor_value() == pytest.approx(161.28, abs=0.05)

    def test_uses_rate2_when_active(self):
        calc = _push_helpers(CachedSOCCalculator())
        sensor = CostToTargetSocSensor(
            EveusTestUpdater(
                {"sessionEnergy": "16", "activeTarif": 1, "tarifAValue": 216}
            ),
            1,
            calc,
        )
        assert sensor._get_sensor_value() == pytest.approx(80.64, abs=0.05)

    def test_unknown_without_tariff(self):
        calc = _push_helpers(CachedSOCCalculator())
        sensor = CostToTargetSocSensor(
            EveusTestUpdater({"sessionEnergy": "16"}), 1, calc
        )
        assert sensor._get_sensor_value() is None

    def test_zero_cost_when_target_reached(self):
        calc = _push_helpers(CachedSOCCalculator())
        calc.set_value("target_soc", 20.0)
        sensor = CostToTargetSocSensor(
            EveusTestUpdater({"sessionEnergy": "0", "activeTarif": 0, "tarif": 432}),
            1,
            calc,
        )
        assert sensor._get_sensor_value() == 0.0


# =============================================================================
# #16 Transition-aware burst polling
# =============================================================================

from conftest import TEST_PASSWORD, TEST_USERNAME
from custom_components.eveus.common_network import EveusUpdater


class _Hass:
    loop = None


def _updater() -> EveusUpdater:
    return EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())


class TestTransitionBurstPolling:
    def _burst_counter(self, updater, monkeypatch):
        calls = []
        monkeypatch.setattr(
            updater, "_schedule_post_command_refresh", lambda: calls.append(1)
        )
        return calls

    def test_state_transition_triggers_burst(self, monkeypatch):
        updater = _updater()
        calls = self._burst_counter(updater, monkeypatch)
        updater._record_success(0.1, {"state": 2})
        updater._record_success(0.1, {"state": 4})
        assert len(calls) == 1

    def test_first_poll_does_not_burst(self, monkeypatch):
        updater = _updater()
        calls = self._burst_counter(updater, monkeypatch)
        updater._record_success(0.1, {"state": 4})
        assert calls == []

    def test_unchanged_state_does_not_burst(self, monkeypatch):
        updater = _updater()
        calls = self._burst_counter(updater, monkeypatch)
        updater._record_success(0.1, {"state": 4})
        updater._record_success(0.1, {"state": 4})
        assert calls == []

    def test_flapping_state_is_debounced(self, monkeypatch):
        updater = _updater()
        calls = self._burst_counter(updater, monkeypatch)
        updater._record_success(0.1, {"state": 2})
        updater._record_success(0.1, {"state": 4})
        updater._record_success(0.1, {"state": 2})
        updater._record_success(0.1, {"state": 4})
        assert len(calls) == 1

    def test_invalid_state_neither_bursts_nor_clears_memory(self, monkeypatch):
        updater = _updater()
        calls = self._burst_counter(updater, monkeypatch)
        updater._record_success(0.1, {"state": 2})
        updater._record_success(0.1, {})  # validator normally rejects; be safe
        updater._record_success(0.1, {"state": 2})
        assert calls == []


# =============================================================================
# #17 Fast offline reconnect + recovery probation
# =============================================================================

import time

from custom_components.eveus.const import (
    CHARGING_UPDATE_INTERVAL,
    OFFLINE_UPDATE_INTERVAL,
)


def _make_offline(updater: EveusUpdater) -> None:
    updater._consecutive_failures = 11
    updater._last_success_monotonic = time.monotonic() - 700
    updater._last_success_time = time.time() - 700


class TestFastOfflineReconnect:
    def test_offline_poll_cycle_is_at_most_sixty_seconds(self):
        # The user powers the charger off between sessions; when it comes
        # back, it must reappear within one offline cycle. Worst case =
        # OFFLINE_UPDATE_INTERVAL, so that must be 60 s, and the failure
        # backoff deadline must never defer past the next tick.
        assert OFFLINE_UPDATE_INTERVAL <= 60
        updater = _updater()
        _make_offline(updater)
        for _ in range(6):
            updater._record_failure(TimeoutError())
            delay = updater._next_poll_attempt - time.time()
            assert delay <= OFFLINE_UPDATE_INTERVAL
        assert updater.update_interval.total_seconds() == OFFLINE_UPDATE_INTERVAL

    def test_backoff_does_not_escalate_with_continued_failures(self):
        updater = _updater()
        _make_offline(updater)
        updater._record_failure(TimeoutError())
        first = updater._next_poll_attempt - time.time()
        for _ in range(5):
            updater._record_failure(TimeoutError())
        assert updater._next_poll_attempt - time.time() == pytest.approx(first, abs=1.0)

    def test_recovery_needs_two_successes_before_fast_cadence(self):
        updater = _updater()
        _make_offline(updater)
        updater._record_failure(TimeoutError())
        updater._record_success(0.1, {"state": 4})
        assert updater.update_interval.total_seconds() == OFFLINE_UPDATE_INTERVAL
        updater._record_success(0.1, {"state": 4})
        assert updater.update_interval.total_seconds() == OFFLINE_UPDATE_INTERVAL
        updater._record_success(0.1, {"state": 4})
        assert updater.update_interval.total_seconds() == CHARGING_UPDATE_INTERVAL

    def test_single_blip_does_not_enter_probation(self):
        # A lone failed poll (not likely-offline) must not delay fast cadence.
        updater = _updater()
        updater._record_success(0.1, {"state": 4})
        updater._record_failure(TimeoutError())
        updater._record_success(0.1, {"state": 4})
        assert updater.update_interval.total_seconds() == CHARGING_UPDATE_INTERVAL

    def test_force_refresh_bypass_counter_untouched(self):
        updater = _updater()
        _make_offline(updater)
        updater._record_failure(TimeoutError())
        assert updater._force_refresh_requests == 0


# =============================================================================
# #49 Charger clock-drift notice
# =============================================================================

from custom_components.eveus import _ClockDriftTracker


def _payload(drift_seconds: float, tz: int = 3) -> dict:
    return {
        "systemTime": int(time.time() + drift_seconds + tz * 3600),
        "timeZone": tz,
    }


class TestClockDriftTracker:
    def test_fires_after_three_polls_above_ten_minutes(self):
        tracker = _ClockDriftTracker()
        assert tracker.evaluate(_payload(900)) is None
        assert tracker.evaluate(_payload(900)) is None
        assert tracker.evaluate(_payload(900)) is True

    def test_negative_drift_also_fires(self):
        tracker = _ClockDriftTracker()
        for _ in range(2):
            tracker.evaluate(_payload(-900))
        assert tracker.evaluate(_payload(-900)) is True

    def test_small_drift_never_fires_and_clears_after_two_polls(self):
        tracker = _ClockDriftTracker()
        for _ in range(3):
            tracker.evaluate(_payload(900))
        assert tracker.evaluate(_payload(30)) is None
        assert tracker.evaluate(_payload(30)) is False

    def test_one_in_sync_poll_resets_debounce(self):
        tracker = _ClockDriftTracker()
        tracker.evaluate(_payload(900))
        tracker.evaluate(_payload(900))
        tracker.evaluate(_payload(0))
        assert tracker.evaluate(_payload(900)) is None

    def test_missing_or_corrupt_fields_leave_state_unchanged(self):
        tracker = _ClockDriftTracker()
        tracker.evaluate(_payload(900))
        tracker.evaluate(_payload(900))
        assert tracker.evaluate({}) is None
        assert tracker.evaluate({"systemTime": -5, "timeZone": 3}) is None
        assert tracker.evaluate({"systemTime": "x", "timeZone": 99}) is None
        # Streak survived the garbage: the next valid drifted poll fires.
        assert tracker.evaluate(_payload(900)) is True

    def test_translations_cover_clock_drift_in_en_and_uk(self):
        import json
        from pathlib import Path

        base = Path("custom_components/eveus")
        for name in ("translations/en.json", "translations/uk.json", "strings.json"):
            issues = json.loads((base / name).read_text())["issues"]
            assert "clock_drift" in issues, name
            assert "Sync Time" in issues["clock_drift"]["description"] or \
                "Синхрон" in issues["clock_drift"]["description"], name


# =============================================================================
# Issue #4: phases dropdown rejected every choice when submitted as a string
# =============================================================================


def test_user_schema_accepts_phase_count_submitted_as_string():
    # The mobile-app frontend submits select values as strings; the schema
    # must coerce "1"/"3" instead of failing "value must be one of [1, 3]".
    from custom_components.eveus.config_flow import build_user_data_schema
    from custom_components.eveus.const import CONF_PHASES

    schema = build_user_data_schema()
    result = schema(
        {
            "host": TEST_HOST,
            "username": TEST_USERNAME,
            "password": TEST_PASSWORD,
            "model": "16A",
            CONF_PHASES: "3",
            "soc_mode": "basic",
        }
    )
    assert result[CONF_PHASES] == 3


def test_advanced_only_prune_list_covers_target_soc_forecast_sensors():
    from custom_components.eveus import _ADVANCED_ONLY_ENTITIES

    assert ("sensor", "energy_to_target_soc") in _ADVANCED_ONLY_ENTITIES
    assert ("sensor", "cost_to_target_soc") in _ADVANCED_ONLY_ENTITIES
