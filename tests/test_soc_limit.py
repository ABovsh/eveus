import asyncio
from unittest.mock import AsyncMock, MagicMock

from custom_components.eveus.ev_sensors import CachedSOCCalculator
from custom_components.eveus.soc_limit import EVENT_SOC_LIMIT_REACHED, SocLimitController


def _calc(target=80, initial=20, cap=50, corr=0):
    c = CachedSOCCalculator()
    c.set_value("initial_soc", initial)
    c.set_value("battery_capacity", cap)
    c.set_value("soc_correction", corr)
    c.set_value("target_soc", target)
    return c


def _updater(state=4, session_energy=30.0, ok=True, stop_ok=True):
    u = MagicMock()
    u.available = ok
    u.last_update_success = ok
    u.device_number = 1
    u.data = {"state": state, "sessionEnergy": session_energy}
    u.send_command = AsyncMock(return_value=stop_ok)
    return u


def _make(calc, updater):
    hass = MagicMock()
    scheduled, events = [], []

    def _spawn(coro):
        # Run the controller's _stop() coroutine to completion so its
        # send_command + conditional event firing are observable.
        scheduled.append(coro)
        asyncio.run(coro)

    hass.async_create_task = _spawn
    hass.bus.async_fire = lambda etype, data=None: events.append((etype, data))
    ctrl = SocLimitController(hass, updater, calc)
    return ctrl, scheduled, events


def test_fires_stop_once_at_target():
    # initial 20% + 30 kWh on a 50 kWh pack ≈ 80% -> reaches target 80
    calc = _calc(target=80, initial=20, cap=50, corr=0)
    updater = _updater(state=4, session_energy=30.0)
    ctrl, scheduled, events = _make(calc, updater)
    ctrl.set_enabled(True)
    ctrl.process()
    ctrl.process()  # second poll must NOT fire again
    assert len(scheduled) == 1
    updater.send_command.assert_awaited_once_with("evseEnabled", 0)


def test_fires_ha_event_on_successful_stop():
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=20, cap=50, corr=0),
        _updater(state=4, session_energy=30.0),
    )
    ctrl.set_enabled(True)
    ctrl.process()
    assert len(events) == 1
    etype, data = events[0]
    assert etype == EVENT_SOC_LIMIT_REACHED
    assert data["device_number"] == 1
    assert data["target_soc"] == 80


def test_no_event_and_retries_when_stop_fails():
    # RC-001: a failed Stop must not emit the "reached" event and must re-arm
    # so the next poll retries this same session.
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=20, cap=50, corr=0),
        _updater(state=4, session_energy=30.0, stop_ok=False),
    )
    ctrl.set_enabled(True)
    ctrl.process()  # attempt 1 fails
    ctrl.process()  # retries
    assert len(scheduled) == 2
    assert events == []


def test_redundant_enable_does_not_refire():
    # RC-004: re-asserting the switch on while already enabled must not re-arm.
    calc = _calc(target=80, initial=20, cap=50, corr=0)
    ctrl, scheduled, events = _make(calc, _updater(state=4, session_energy=30.0))
    ctrl.set_enabled(True)
    ctrl.process()              # fires (1)
    ctrl.set_enabled(True)      # redundant — must be idempotent
    ctrl.process()              # must NOT fire again
    assert len(scheduled) == 1 and len(events) == 1


def test_does_not_fire_when_disabled():
    ctrl, scheduled, events = _make(_calc(), _updater(session_energy=30.0))
    ctrl.set_enabled(False)
    ctrl.process()
    assert scheduled == [] and events == []


def test_does_not_fire_below_target():
    ctrl, scheduled, events = _make(_calc(target=90), _updater(session_energy=30.0))
    ctrl.set_enabled(True)
    ctrl.process()
    assert scheduled == [] and events == []


def test_ignored_on_failed_poll():
    ctrl, scheduled, events = _make(_calc(), _updater(session_energy=30.0, ok=False))
    ctrl.set_enabled(True)
    ctrl.process()
    assert scheduled == [] and events == []


def test_rearms_after_session_ends():
    calc = _calc(target=80, initial=20, cap=50, corr=0)
    updater = _updater(state=4, session_energy=30.0)
    ctrl, scheduled, events = _make(calc, updater)
    ctrl.set_enabled(True)
    ctrl.process()                       # fires (1)
    updater.data = {"state": 1, "sessionEnergy": 0.0}  # session ended (not charging)
    ctrl.process()                       # re-arm
    updater.data = {"state": 4, "sessionEnergy": 30.0}  # new session at/above target
    ctrl.process()                       # fires again (2)
    assert len(scheduled) == 2 and len(events) == 2
