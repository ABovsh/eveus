"""Regression tests for the adversarial-review hardening round on the rc branch.

Covers: the control-entity grace re-check dispatch, transition-burst
self-cancellation, kWh-precision target estimates, the legacy hw_version
registry cleanup, offline recovery probation, cost-at-target semantics,
monetary metadata, and strict phase-count coercion.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

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

import conftest  # noqa: F401  (installs HA stubs)
from conftest import EV_HELPERS, EveusTestUpdater

from custom_components.eveus.ev_sensors import (
    CachedSOCCalculator,
    CostToTargetSocSensor,
    EnergyToTargetSocSensor,
)


def _push_helpers(calc: CachedSOCCalculator) -> CachedSOCCalculator:
    for entity_id, value in EV_HELPERS.items():
        calc.set_value(entity_id.removeprefix("input_number.ev_"), float(value))
    return calc


# ---------------------------------------------------------------------------
# A01 — control-entity grace re-check must dispatch without TypeError
# ---------------------------------------------------------------------------


def test_control_mixin_availability_accepts_recheck_kwargs() -> None:
    from custom_components.eveus.common_base import BaseEveusEntity, ControlEntityMixin

    class _Control(ControlEntityMixin, BaseEveusEntity):
        ENTITY_NAME = "Probe Control"

    entity = _Control(EveusTestUpdater({}), 1)
    entity._updater._available = False
    # The scheduled grace re-check calls polymorphically with the base kwargs;
    # the mixin override must accept them instead of raising TypeError.
    result = entity._update_availability_state(
        grace_period=30, label="Entity", clear_optimistic_state=True
    )
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# A02 — a tracked refresh must not cancel itself when it triggers a burst
# ---------------------------------------------------------------------------


def test_cancel_pending_refreshes_skips_current_task() -> None:
    from custom_components.eveus.common_network import EveusUpdater

    updater = EveusUpdater.__new__(EveusUpdater)
    updater._pending_refresh_unsubs = []

    async def _scenario() -> bool:
        cancelled_self = False

        async def _tracked() -> None:
            nonlocal cancelled_self
            try:
                # Simulate the refresh observing a transition and rescheduling
                # the burst from inside its own tracked task.
                updater._cancel_pending_refreshes()
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                cancelled_self = True
                raise

        task = asyncio.ensure_future(_tracked())
        updater._post_command_refresh_tasks = [task]
        try:
            await task
        except asyncio.CancelledError:
            pass
        return cancelled_self

    assert asyncio.run(_scenario()) is False


# ---------------------------------------------------------------------------
# A06 — remaining energy computed in kWh, not whole-percent SOC
# ---------------------------------------------------------------------------


def test_energy_to_target_does_not_zero_from_percent_rounding() -> None:
    # 80 kWh battery, initial 20%: 16 kWh. Session 56.4 kWh at 10% loss adds
    # 50.76 kWh -> 66.76 kWh = 83.45% which ROUNDS to 83%... use a case where
    # the rounded percent reaches the target while real kWh remain:
    # target 84% = 67.2 kWh, current 66.76 kWh -> 0.44 kWh battery remaining.
    calc = _push_helpers(CachedSOCCalculator())
    calc.set_value("target_soc", 84.0)
    sensor = EnergyToTargetSocSensor(
        EveusTestUpdater({"sessionEnergy": "56.4"}), 1, calc
    )
    value = sensor._get_sensor_value()
    assert value is not None
    # 0.44 kWh battery / 0.9 efficiency ≈ 0.49 kWh from the grid — must not be 0.
    assert value == pytest.approx(0.49, abs=0.02)


# ---------------------------------------------------------------------------
# A07 — legacy hw_version cleared even when device info was final at startup
# ---------------------------------------------------------------------------


def test_registry_hw_version_cleared_when_info_already_final(monkeypatch) -> None:
    from custom_components.eveus import common_base
    from custom_components.eveus.common_base import BaseEveusEntity

    class _Sensor(BaseEveusEntity):
        ENTITY_NAME = "Probe Sensor"

    updater = EveusTestUpdater({"verFWMain": "GRM070A-R3.05.2"})
    entity = _Sensor(updater, 1)
    entity.hass = SimpleNamespace()
    assert entity._device_info_finalized is True  # firmware known at construction

    updated: dict = {}

    class _Registry:
        def async_get_device(self, identifiers):
            return SimpleNamespace(id="dev1")

        def async_update_device(self, device_id, **kwargs):
            updated.update(kwargs)

    monkeypatch.setattr(
        common_base.dr, "async_get", lambda hass: _Registry(), raising=False
    )

    entity._maybe_finalize_device_info()
    assert updated.get("hw_version", "missing") is None
    assert updater._device_registry_finalized is True

    # And the write happens only once per runtime.
    updated.clear()
    entity._maybe_finalize_device_info()
    assert updated == {}


# ---------------------------------------------------------------------------
# A09 — a failed poll during recovery probation restarts the probation
# ---------------------------------------------------------------------------


def test_failure_during_probation_resets_counter() -> None:
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


# ---------------------------------------------------------------------------
# A11/A12 — cost at target is 0 without tariff data; monetary metadata
# ---------------------------------------------------------------------------


def test_cost_to_target_zero_at_target_without_tariff() -> None:
    calc = _push_helpers(CachedSOCCalculator())
    calc.set_value("target_soc", 20.0)  # already at target
    sensor = CostToTargetSocSensor(EveusTestUpdater({"sessionEnergy": "0"}), 1, calc)
    # No tariff fields in the payload at all — cost is still exactly zero.
    assert sensor._get_sensor_value() == 0.0


def test_cost_to_target_monetary_metadata() -> None:
    from homeassistant.components.sensor import SensorDeviceClass

    calc = _push_helpers(CachedSOCCalculator())
    sensor = CostToTargetSocSensor(EveusTestUpdater({}), 1, calc)
    assert sensor._attr_device_class == SensorDeviceClass.MONETARY
    assert sensor._attr_state_class is None
    assert sensor._attr_native_unit_of_measurement == "UAH"


# ---------------------------------------------------------------------------
# A13 — fractional phase counts are rejected, not truncated
# ---------------------------------------------------------------------------


def test_normalize_user_input_rejects_fractional_phases() -> None:
    import voluptuous as vol

    from custom_components.eveus.config_flow import normalize_user_input
    from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

    base = {
        "host": TEST_HOST,
        "username": TEST_USERNAME,
        "password": TEST_PASSWORD,
        "model": "16A",
    }
    with pytest.raises(vol.Invalid):
        normalize_user_input({**base, "phases": 2.9})
    # Integral floats (JSON numbers) keep working.
    assert normalize_user_input({**base, "phases": 3.0})["phases"] == 3


def test_phases_whole_number_and_bool_rejected_by_normalize() -> None:
    # Phase-count hardening now lives in normalize_user_input (the schema must
    # stay serializable for the frontend — issue #8). A whole 3 is accepted; a
    # fractional or boolean phase count is rejected.
    import voluptuous as vol

    from custom_components.eveus.config_flow import normalize_user_input

    base = {
        "host": "1.2.3.4",
        "username": "eveus",
        "password": "secret",
        "model": "16A",
    }
    assert normalize_user_input({**base, "phases": 3})["phases"] == 3
    for bad in (3.9, True):
        with pytest.raises(vol.Invalid):
            normalize_user_input({**base, "phases": bad})


# ---------------------------------------------------------------------------
# Round 2 adversarial findings (R2-A09/A14/A15/A16)
# ---------------------------------------------------------------------------


def test_clock_drift_does_not_clear_while_still_minutes_wrong() -> None:
    # R2-A09: with the warning active, drift dropping just under the trigger
    # threshold (still ~9 minutes wrong) must NOT clear the notice; only a
    # genuinely re-synced clock (below the clear threshold) does.
    import time as _t

    from custom_components.eveus import _ClockDriftTracker

    def _payload(drift: float, tz: int = 3) -> dict:
        return {"systemTime": int(_t.time() + drift + tz * 3600), "timeZone": tz}

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


def test_clock_drift_hover_then_resync_needs_consecutive_in_sync_polls() -> None:
    # R2-A09: in-sync polls interleaved with hysteresis-band polls must not
    # accumulate toward clearing — the clear streak is consecutive.
    import time as _t

    from custom_components.eveus import _ClockDriftTracker

    def _payload(drift: float, tz: int = 3) -> dict:
        return {"systemTime": int(_t.time() + drift + tz * 3600), "timeZone": tz}

    tracker = _ClockDriftTracker()
    for _ in range(3):
        tracker.evaluate(_payload(900))
    assert tracker.evaluate(_payload(10)) is None
    assert tracker.evaluate(_payload(500)) is None  # band: resets the streak
    assert tracker.evaluate(_payload(10)) is None
    assert tracker.evaluate(_payload(10)) is False


def test_energy_to_target_unknown_when_session_energy_absent_mid_session() -> None:
    # R2-A14: firmware dropping sessionEnergy during an ACTIVE session must
    # blank the forecast, not reset it to the full from-initial-SOC estimate.
    calc = _push_helpers(CachedSOCCalculator())
    sensor = EnergyToTargetSocSensor(
        EveusTestUpdater({"state": 4}), 1, calc
    )
    assert sensor._get_sensor_value() is None


def test_energy_to_target_zero_fallback_outside_active_session() -> None:
    # R2-A14: before a session starts (idle charger, no sessionEnergy field)
    # the 0-kWh-delivered fallback keeps producing the full estimate.
    calc = _push_helpers(CachedSOCCalculator())
    sensor = EnergyToTargetSocSensor(
        EveusTestUpdater({"state": 2}), 1, calc
    )
    assert sensor._get_sensor_value() is not None


def test_energy_to_target_has_no_storage_device_class() -> None:
    # R2-A15: the sensor reports energy still NEEDED from the grid, not energy
    # currently stored — ENERGY_STORAGE semantics would mislabel it.
    calc = _push_helpers(CachedSOCCalculator())
    sensor = EnergyToTargetSocSensor(EveusTestUpdater({}), 1, calc)
    assert sensor.device_class is None
    assert sensor._attr_native_unit_of_measurement == "kWh"


def test_diagnostics_heuristic_redacts_credential_like_keys() -> None:
    # R2-A16: common credential-ish names must hit the heuristic, nested too.
    from custom_components.eveus.diagnostics import _sensitive_keys

    data = {
        "api_key": "x",
        "authorization": "x",
        "credentials": {"private_key": "x"},
        "pwd": "x",
        "battery_capacity": 80,
        "phases": 3,
    }
    keys = _sensitive_keys(data)
    for k in ("api_key", "authorization", "credentials", "private_key", "pwd"):
        assert k in keys, k
    assert "battery_capacity" not in keys
    assert "phases" not in keys
