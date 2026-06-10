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
        assert info["sw_version"] == "GRM070A-R3.05.2"

    def test_device_info_omits_hw_version_even_with_legacy_hardware_key(self):
        info = utils.get_device_info(TEST_HOST, {"verFWMain": "x1", "hardware": "h1"})
        assert "hw_version" not in info


# =============================================================================
# #15 Energy & Cost to Target SOC (Advanced mode only)
# =============================================================================

import pytest

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


def test_advanced_only_prune_list_covers_target_soc_forecast_sensors():
    from custom_components.eveus import _ADVANCED_ONLY_ENTITIES

    assert ("sensor", "energy_to_target_soc") in _ADVANCED_ONLY_ENTITIES
    assert ("sensor", "cost_to_target_soc") in _ADVANCED_ONLY_ENTITIES
