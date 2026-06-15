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
    ctrl.process()                                     # sends Stop, awaits confirm
    updater.data = {"state": 1, "sessionEnergy": 0.0}  # charger actually stopped
    ctrl.process()                                     # confirms; no second Stop
    assert len(scheduled) == 1
    updater.send_command.assert_awaited_once_with("evseEnabled", 0)


def test_fires_ha_event_only_after_stop_confirmed():
    # The event means the charge ACTUALLY stopped, not merely that the POST
    # returned 2xx — it fires only once the session leaves the active states.
    updater = _updater(state=4, session_energy=30.0)
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=20, cap=50, corr=0), updater
    )
    ctrl.set_enabled(True)
    ctrl.process()
    assert events == []                                # POST sent, not confirmed
    updater.data = {"state": 1, "sessionEnergy": 0.0}  # session ended -> confirmed
    ctrl.process()
    assert len(events) == 1
    etype, data = events[0]
    assert etype == EVENT_SOC_LIMIT_REACHED
    assert data["device_number"] == 1
    assert data["target_soc"] == 80


def test_no_event_and_keeps_enforcing_while_charging_continues():
    # F1: a Stop the charger accepted (2xx) but did NOT honour — charging stays
    # active — must never emit a false "reached" event and must keep re-sending
    # the Stop each poll instead of latching as done.
    updater = _updater(state=4, session_energy=30.0)  # never leaves active state
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=20, cap=50, corr=0), updater
    )
    ctrl.set_enabled(True)
    ctrl.process()
    ctrl.process()
    ctrl.process()
    assert len(scheduled) >= 2   # kept enforcing rather than latching
    assert events == []          # never falsely reported a stop


def test_no_event_and_retries_when_stop_fails():
    # RC-001: a failed Stop must not emit the "reached" event and must retry
    # this same session.
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
    updater = _updater(state=4, session_energy=30.0)
    ctrl, scheduled, events = _make(calc, updater)
    ctrl.set_enabled(True)
    ctrl.process()                                     # Stop sent
    updater.data = {"state": 1, "sessionEnergy": 0.0}  # stopped -> confirm, re-arm
    ctrl.process()
    assert len(events) == 1
    ctrl.set_enabled(True)                             # redundant — idempotent
    ctrl.process()                                     # session ended; nothing
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
    ctrl.process()                       # session 1: Stop sent
    updater.data = {"state": 1, "sessionEnergy": 0.0}  # session 1 ended -> confirm (1)
    ctrl.process()
    updater.data = {"state": 4, "sessionEnergy": 30.0}  # new session at/above target
    ctrl.process()                       # session 2: Stop sent
    updater.data = {"state": 1, "sessionEnergy": 0.0}  # session 2 ended -> confirm (2)
    ctrl.process()
    assert len(scheduled) == 2 and len(events) == 2


def test_inflight_stop_superseded_by_toggle_fires_nothing():
    # Adversarial: a Stop awaiting send_command when the limit is toggled
    # off-then-on is a stale (older-generation) attempt — it must be cancelled
    # and must neither fire the event nor corrupt the new epoch's latch.
    async def scenario():
        calc = _calc(target=80, initial=20, cap=50, corr=0)
        gate = asyncio.Event()
        events = []

        async def slow_send(_cmd, _val):
            await gate.wait()
            return True

        updater = MagicMock()
        updater.available = True
        updater.last_update_success = True
        updater.device_number = 1
        updater.data = {"state": 4, "sessionEnergy": 30.0}
        updater.send_command = slow_send

        hass = MagicMock()
        hass.async_create_task = lambda coro: asyncio.ensure_future(coro)
        hass.bus.async_fire = lambda et, data=None: events.append((et, data))

        ctrl = SocLimitController(hass, updater, calc)
        ctrl.set_enabled(True)
        ctrl.process()              # spawns attempt A, which blocks on the gate
        await asyncio.sleep(0)      # let A reach `await gate.wait()`
        ctrl.set_enabled(False)     # cancels A, bumps the generation
        ctrl.set_enabled(True)      # re-arm into a fresh generation
        gate.set()                  # release A (now superseded/cancelled)
        await asyncio.sleep(0.02)   # let the event loop settle
        assert events == []         # the superseded attempt fired nothing
        assert ctrl._fired is False  # new epoch's latch is clean

    asyncio.run(scenario())
