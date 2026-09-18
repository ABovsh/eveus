"""SOC limit and SOC sensor regressions: latching across reloads and missed
polls, the suspendLimits recheck, restore validation, and unknown-vs-zero."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from conftest import SnapshotBackedMock
from custom_components.eveus.common_base import BaseEveusEntity
from custom_components.eveus.ev_sensors import CachedSOCCalculator
from custom_components.eveus.soc_limit import SocLimitController
from custom_components.eveus.switch import EveusSocLimitSwitch
from conftest import EV_HELPERS, EveusTestUpdater
from custom_components.eveus.ev_sensors import EVSocKwhSensor, EVSocPercentSensor
from test_ev_sensor_entities import push_helpers
from types import SimpleNamespace  # noqa: E402
from test_soc_autofill import (  # noqa: E402
    _build as _build_initial_soc,
    _no_dispatcher,  # noqa: F401 - autouse fixture, applies by being imported here
    _poll as _poll_soc,
)
from test_soc_autofill import _build, _poll  # noqa: F401  (fixtures come along)


def _calc(target=80, initial=20, cap=50, corr=0):
    c = CachedSOCCalculator()
    c.set_value("initial_soc", initial)
    c.set_value("battery_capacity", cap)
    c.set_value("soc_correction", corr)
    c.set_value("target_soc", target)
    return c


def _updater(session_energy=30.0, session_time=3600):
    u = SnapshotBackedMock()
    u.available = True
    u.last_update_success = True
    u.device_number = 1
    u.data = {
        "state": 4,
        "sessionEnergy": session_energy,
        "sessionTime": session_time,
        "evseEnabled": 0,
        "suspendLimits": 0,
    }
    u.send_command = AsyncMock(return_value=True)
    return u


def _controller(calc, updater):
    hass = MagicMock()
    events = []
    hass.async_create_task = lambda coro: asyncio.run(coro)
    hass.bus.async_fire = lambda etype, data=None: events.append((etype, data))
    return SocLimitController(hass, updater, calc), events


def test_soc_switch_reenabled_while_suspended_survives_reload() -> None:
    controller = MagicMock()
    updater = MagicMock()
    updater.config_entry = MagicMock()
    # Fresh poll the setup performed before adding platforms: still suspended.
    updater.data = {"suspendLimits": 1}

    sw = EveusSocLimitSwitch(updater, controller, device_number=1)
    sw.hass = MagicMock()
    sw.async_write_ha_state = MagicMock()
    last = MagicMock()
    last.state = "on"
    sw.async_get_last_state = AsyncMock(return_value=last)

    with patch.object(BaseEveusEntity, "async_added_to_hass", AsyncMock()):
        asyncio.run(sw.async_added_to_hass())

    assert sw.is_on is True
    # First post-reload poll, master still on: must NOT be read as a fresh edge.
    sw._updater.data = {"suspendLimits": 1}
    sw._handle_coordinator_update()
    assert sw.is_on is True


def test_soc_limit_latched_through_session_begun_between_polls() -> None:
    calc = _calc()
    updater = _updater(session_energy=30.0, session_time=3600)
    ctrl, events = _controller(calc, updater)
    ctrl.set_enabled(True)

    ctrl.process()  # at target -> Stop sent, attempt pending
    updater.data = {**updater.data, "evseEnabled": 1}
    ctrl.process()  # charger confirms -> event fires once, _fired latched
    assert len(events) == 1
    assert updater.send_command.call_count == 1

    # Session B begins entirely between polls (no inactive poll observed):
    # active, counters reset, charging, already back at target. The limit stays
    # latched and must NOT re-fire off a boundary it never saw.
    updater.data = {
        "state": 4,
        "sessionEnergy": 30.0,
        "sessionTime": 5,
        "evseEnabled": 0,
        "suspendLimits": 0,
    }
    ctrl.process()
    assert updater.send_command.call_count == 1  # no second Stop
    assert len(events) == 1


def test_soc_limit_refires_after_an_observed_session_boundary() -> None:
    calc = _calc()
    updater = _updater(session_energy=30.0, session_time=3600)
    ctrl, events = _controller(calc, updater)
    ctrl.set_enabled(True)

    ctrl.process()
    updater.data = {**updater.data, "evseEnabled": 1}
    ctrl.process()  # fired once
    assert len(events) == 1

    # Session actually ends (inactive state observed) -> re-arm.
    updater.data = {**updater.data, "state": 1, "evseEnabled": 1}
    ctrl.process()

    # New session, at target again -> stops again (every session while enabled).
    updater.data = {
        "state": 4,
        "sessionEnergy": 30.0,
        "sessionTime": 5,
        "evseEnabled": 0,
        "suspendLimits": 0,
    }
    ctrl.process()
    assert updater.send_command.call_count == 2
    updater.data = {**updater.data, "evseEnabled": 1}
    ctrl.process()
    assert len(events) == 2

def test_zero_target_soc_does_not_stop_charging_at_session_start():
    from custom_components.eveus.ev_sensors import CachedSOCCalculator
    from custom_components.eveus.soc_limit import SocLimitController

    calc = CachedSOCCalculator()
    calc.set_value("initial_soc", 20)
    calc.set_value("battery_capacity", 50)
    calc.set_value("soc_correction", 0)
    calc.set_value("target_soc", 0)

    updater = MagicMock()
    updater.available = True
    updater.last_update_success = True
    updater.device_number = 1
    updater.data = {
        "state": 4,
        "sessionEnergy": 0.0,
        "evseEnabled": 0,
        "suspendLimits": 0,
    }
    updater.send_command = AsyncMock(return_value=True)

    hass = MagicMock()
    hass.async_create_task = MagicMock()
    hass.bus.async_fire = MagicMock()

    ctrl = SocLimitController(hass, updater, calc)
    ctrl.set_enabled(True)
    ctrl.process()

    hass.async_create_task.assert_not_called()
    hass.bus.async_fire.assert_not_called()


def test_stop_aborts_if_suspend_limits_enabled_mid_flight():
    """Regression test for D05: process() only checked suspendLimits when it
    scheduled the Stop task; if "Disable limits" was toggled on while the task
    was queued, _stop() must recheck before POSTing rather than sending a
    command the safety contract says to stand down from.
    """
    from custom_components.eveus.ev_sensors import CachedSOCCalculator
    from custom_components.eveus.soc_limit import SocLimitController

    calc = CachedSOCCalculator()
    calc.set_value("initial_soc", 20)
    calc.set_value("battery_capacity", 50)
    calc.set_value("soc_correction", 0)
    calc.set_value("target_soc", 80)

    updater = MagicMock()
    updater.available = True
    updater.last_update_success = True
    updater.device_number = 1
    updater.data = {
        "state": 4,
        "sessionEnergy": 30.0,
        "evseEnabled": 0,
        "suspendLimits": 0,
    }
    updater.send_command = AsyncMock(return_value=True)

    hass = MagicMock()

    def _spawn(coro):
        # Simulate "Disable limits" being toggled on after process() decided
        # to stop but before the queued task actually runs.
        updater.data = {**updater.data, "suspendLimits": 1}
        asyncio.run(coro)

    hass.async_create_task = _spawn
    hass.bus.async_fire = MagicMock()

    ctrl = SocLimitController(hass, updater, calc)
    ctrl.set_enabled(True)
    ctrl.process()

    updater.send_command.assert_not_called()
    hass.bus.async_fire.assert_not_called()


def test_soc_number_restore_rejects_bool_native_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test for D04: bool is an int subclass, so float(False) == 0.0
    with no exception — a corrupt RestoreNumber state of False must not sneak
    through as a valid in-range restored value (e.g. Target SOC silently
    becoming 0, which per D03 would otherwise stop charging instantly).
    """
    from conftest import EveusTestUpdater, HelperHass
    from custom_components.eveus import number as number_module
    from custom_components.eveus.ev_sensors import CachedSOCCalculator
    from custom_components.eveus.number import EveusTargetSocNumber

    async def noop_super(self):
        return None

    monkeypatch.setattr(number_module.BaseEveusEntity, "async_added_to_hass", noop_super)
    monkeypatch.setattr(number_module, "async_dispatcher_send", lambda *a, **k: None)

    updater = EveusTestUpdater({})
    calc = CachedSOCCalculator()
    entity = EveusTargetSocNumber(updater, calc, seed=80, device_number=1)
    entity.hass = HelperHass({})

    async def fake_last(self):

        return SimpleNamespace(native_value=False)

    monkeypatch.setattr(type(entity), "async_get_last_number_data", fake_last, raising=False)

    asyncio.run(number_module.EveusSocConfigNumber.async_added_to_hass(entity))

    assert entity.native_value == 80  # seed kept, bool restore rejected
    assert calc.target_soc == 80

def _sensor(cls, data: dict):
    sensor = cls(EveusTestUpdater(data))
    push_helpers(sensor._soc_calculator, EV_HELPERS)
    return sensor


def test_soc_kwh_goes_unknown_when_session_energy_absent_mid_session() -> None:
    """An active session with sessionEnergy missing must not read as 0 kWh delivered."""
    sensor = _sensor(EVSocKwhSensor, {"state": 4})
    assert sensor._get_sensor_value() is None


def test_soc_percent_goes_unknown_when_session_energy_absent_mid_session() -> None:
    """Same for SOC Percent: 0 delivered would snap the graph down to Initial SOC."""
    sensor = _sensor(EVSocPercentSensor, {"state": 4})
    assert sensor._get_sensor_value() is None


def test_soc_sensors_still_use_zero_fallback_outside_a_session() -> None:
    """Before a session starts, an absent sessionEnergy still means 0 delivered."""
    kwh = _sensor(EVSocKwhSensor, {"state": 2})
    pct = _sensor(EVSocPercentSensor, {"state": 2})
    assert kwh._get_sensor_value() is not None
    assert pct._get_sensor_value() == 20


def test_seeding_is_skipped_when_session_energy_is_absent() -> None:
    """Mid-session the field being absent is anomalous telemetry, not 0 kWh.

    A session start observed late already has energy on the meter; reading an
    absent field as zero would copy the car's SOC in un-rebased and overstate
    every SOC figure for the rest of the session. Same rule the SOC sensors
    follow.
    """
    entity, updater, _ = _build_initial_soc(car_soc=55, seed=20)

    _poll_soc(entity, updater, 3)
    _poll_soc(entity, updater, 4, session_energy=None)

    assert entity.native_value == 20

def test_a_repeating_seed_failure_reports_one_steady_reason() -> None:
    """`soc_anchor` on SOC Percent mirrors this text.

    The rebase subtracts the energy delivered so far, so a reason quoting it
    reads differently on every poll of a running session — a recorder row per
    poll for the rest of the cycle, on the entity whose attribute it is.
    """
    entity, updater, calc = _build(car_soc=10, seed=20)

    _poll(entity, updater, 4, session_energy=20.0)
    first = dict(calc.last_seed)
    assert first["seeded"] is False

    _poll(entity, updater, 4, session_energy=21.0)

    assert calc.last_seed == first
