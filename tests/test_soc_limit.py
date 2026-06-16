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


def _updater(state=4, session_energy=30.0, ok=True, stop_ok=True, ev=0):
    # evseEnabled polarity matches the firmware: 0 = charging (go), 1 = stopped.
    # An active charge therefore defaults to evseEnabled=0.
    u = MagicMock()
    u.available = ok
    u.last_update_success = ok
    u.device_number = 1
    # suspendLimits=0 is the normal "master Disable-limits OFF" state; the
    # controller only enforces when it reads a clean 0 (a missing/garbled value
    # means the master state is unknown and it stands down — see V-03).
    u.data = {
        "state": state,
        "sessionEnergy": session_energy,
        "evseEnabled": ev,
        "suspendLimits": 0,
    }
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


def _confirm(updater, state=4):
    """Simulate the charger acknowledging our Stop: evseEnabled rises to 1."""
    updater.data = {**updater.data, "state": state, "evseEnabled": 1}


def test_fires_stop_once_at_target():
    # initial 20% + 30 kWh on a 50 kWh pack ≈ 80% -> reaches target 80
    calc = _calc(target=80, initial=20, cap=50, corr=0)
    updater = _updater(state=4, session_energy=30.0, ev=0)
    ctrl, scheduled, events = _make(calc, updater)
    ctrl.set_enabled(True)
    ctrl.process()              # sends Stop; charger still evseEnabled=0
    _confirm(updater)           # charger reports evseEnabled=1
    ctrl.process()              # confirms; no second Stop
    assert len(scheduled) == 1
    updater.send_command.assert_awaited_once_with("evseEnabled", 1)


def test_fires_ha_event_only_after_evse_disabled_confirmed():
    # The event means the charge ACTUALLY stopped (charger reports evseEnabled=1),
    # not merely that the Stop POST returned 2xx.
    updater = _updater(state=4, session_energy=30.0, ev=0)
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=20, cap=50, corr=0), updater
    )
    ctrl.set_enabled(True)
    ctrl.process()
    assert events == []         # POST sent, charger still enabled -> unconfirmed
    _confirm(updater)
    ctrl.process()
    assert len(events) == 1
    etype, data = events[0]
    assert etype == EVENT_SOC_LIMIT_REACHED
    assert data["device_number"] == 1
    assert data["target_soc"] == 80


def test_no_event_and_keeps_enforcing_while_charging_continues():
    # F1: a Stop the charger accepted (2xx) but did NOT honour — evseEnabled stays
    # 0 — must never emit a false "reached" event and must keep re-sending Stop.
    updater = _updater(state=4, session_energy=30.0, ev=0)  # stays charging
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=20, cap=50, corr=0), updater
    )
    ctrl.set_enabled(True)
    ctrl.process()
    ctrl.process()
    ctrl.process()
    assert len(scheduled) >= 2   # kept enforcing rather than latching
    assert events == []          # never falsely reported a stop


def test_unrelated_unplug_does_not_emit():
    # F3: the session ending with evseEnabled still 0 (an unplug/error, NOT our
    # Stop) must not be misattributed as a confirmed SOC-limit stop.
    updater = _updater(state=4, session_energy=30.0, ev=0)
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=20, cap=50, corr=0), updater
    )
    ctrl.set_enabled(True)
    ctrl.process()                       # Stop sent; awaiting evseEnabled=1
    # Session ends but the charger never reported our stop -> not our doing.
    updater.data = {"state": 1, "sessionEnergy": 0.0, "evseEnabled": 0, "suspendLimits": 0}
    ctrl.process()
    assert events == []


def test_confirmation_held_across_retry_then_session_end():
    # F3: the confirmation token must survive a retry and a same-poll session end
    # (with evseEnabled=1) — it must NOT be lost.
    updater = _updater(state=4, session_energy=30.0, ev=0)
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=20, cap=50, corr=0), updater
    )
    ctrl.set_enabled(True)
    ctrl.process()                       # Stop sent
    ctrl.process()                       # still charging -> retry, token held
    updater.data = {"state": 1, "sessionEnergy": 0.0, "evseEnabled": 1, "suspendLimits": 0}  # ended + stopped
    ctrl.process()                       # confirms here, not lost
    assert len(events) == 1


def test_does_not_issue_or_emit_when_already_disabled_at_target():
    # F5: an at-target payload already reporting evseEnabled=1 (already stopped)
    # must NOT issue a Stop nor be misread as confirmed — there is no 0->1
    # transition we caused.
    updater = _updater(state=4, session_energy=30.0, ev=1)
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=20, cap=50, corr=0), updater
    )
    ctrl.set_enabled(True)
    ctrl.process()
    ctrl.process()
    assert scheduled == [] and events == []


def test_missing_evse_at_active_poll_is_skipped_then_confirms():
    # An active poll missing evseEnabled is skipped (no boundary, nothing lost);
    # the pending token stays armed and a later complete poll confirms.
    updater = _updater(state=4, session_energy=30.0, ev=0)
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=20, cap=50, corr=0), updater
    )
    ctrl.set_enabled(True)
    ctrl.process()                                          # Stop sent (evse was 0)
    updater.data = {"state": 4, "sessionEnergy": 30.0, "suspendLimits": 0}      # active, no evseEnabled
    ctrl.process()
    assert events == []                                     # skipped, token held
    updater.data = {"state": 4, "sessionEnergy": 30.0, "evseEnabled": 1, "suspendLimits": 0}
    ctrl.process()                                          # now confirmed
    assert len(events) == 1


def test_missing_evse_at_session_end_discards_token_no_cross_session_emit():
    # F6/F7: a session-end poll omitting evseEnabled re-arms from `state` alone
    # (safety: an unconfirmable attempt is discarded, never emitted). The token
    # must NOT bleed into the next session where an unrelated disable would
    # falsely confirm the previous session's Stop.
    updater = _updater(state=4, session_energy=30.0, ev=0)
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=20, cap=50, corr=0), updater
    )
    ctrl.set_enabled(True)
    ctrl.process()                                          # Stop sent (evse was 0)
    updater.data = {"state": 1, "sessionEnergy": 0.0, "suspendLimits": 0}       # session end, no evseEnabled
    ctrl.process()                                          # boundary -> discard token
    assert events == []
    # New session well below target; an unrelated stop must emit nothing.
    updater.data = {"state": 4, "sessionEnergy": 5.0, "evseEnabled": 0, "suspendLimits": 0}
    ctrl.process()
    updater.data = {"state": 4, "sessionEnergy": 5.0, "evseEnabled": 1, "suspendLimits": 0}
    ctrl.process()
    assert events == []


def test_hidden_boundary_via_failed_polls_does_not_bleed():
    # F8: failed/unavailable polls can hide the inactive boundary entirely. The
    # pending token must be bound to the session: when a later active poll shows
    # sessionEnergy reset (new session), discard it so an unrelated disable in the
    # new session cannot falsely confirm the old session's Stop.
    updater = _updater(state=4, session_energy=30.0, ev=0)
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=20, cap=50, corr=0), updater
    )
    ctrl.set_enabled(True)
    ctrl.process()                       # session A: Stop sent at 30 kWh
    # Boundary (A ending, B starting) hidden by failed polls -> next observed poll
    # is B, active, with a RESET energy counter and below target.
    updater.data = {"state": 4, "sessionEnergy": 2.0, "evseEnabled": 0, "suspendLimits": 0}
    ctrl.process()                       # energy dropped -> token discarded
    updater.data = {"state": 4, "sessionEnergy": 2.0, "evseEnabled": 1, "suspendLimits": 0}
    ctrl.process()                       # unrelated stop in B
    assert events == []                  # A's event must NOT fire in B


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
    updater = _updater(state=4, session_energy=30.0, ev=0)
    ctrl, scheduled, events = _make(calc, updater)
    ctrl.set_enabled(True)
    ctrl.process()                       # Stop sent
    _confirm(updater)                    # confirmed -> event
    ctrl.process()
    assert len(events) == 1
    ctrl.set_enabled(True)               # redundant — idempotent
    ctrl.process()                       # already fired; nothing
    assert len(scheduled) == 1 and len(events) == 1


def test_does_not_fire_when_disabled():
    ctrl, scheduled, events = _make(_calc(), _updater(session_energy=30.0))
    ctrl.set_enabled(False)
    ctrl.process()
    assert scheduled == [] and events == []


def test_disable_all_limits_stands_down_then_resumes():
    updater = _updater(state=4, session_energy=30.0, ev=0)
    updater.data["suspendLimits"] = 1
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=20, cap=50, corr=0), updater
    )
    ctrl.set_enabled(True)
    ctrl.process()
    assert scheduled == [] and events == []

    updater.data["suspendLimits"] = 0
    ctrl.process()
    _confirm(updater)
    ctrl.process()
    assert len(scheduled) == 1 and len(events) == 1


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
    _confirm(updater)                    # evseEnabled=1 -> confirm (1)
    ctrl.process()
    updater.data = {"state": 1, "sessionEnergy": 0.0, "evseEnabled": 1, "suspendLimits": 0}  # session ends
    ctrl.process()                       # re-arm
    updater.data = {"state": 4, "sessionEnergy": 30.0, "evseEnabled": 0, "suspendLimits": 0}  # new session
    ctrl.process()                       # session 2: Stop sent
    _confirm(updater)                    # evseEnabled=1 -> confirm (2)
    ctrl.process()
    assert len(scheduled) == 2 and len(events) == 2


def test_confirmation_during_inflight_stop_is_not_lost():
    # F9: a poll observing evseEnabled==1 while the Stop POST is still in flight
    # must still confirm — the attempt is recorded before the await, so the
    # boundary re-arm cannot cancel it and lose the event.
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
        updater.data = {"state": 4, "sessionEnergy": 30.0, "evseEnabled": 0, "suspendLimits": 0}
        updater.send_command = slow_send

        hass = MagicMock()
        hass.async_create_task = lambda coro: asyncio.ensure_future(coro)
        hass.bus.async_fire = lambda et, data=None: events.append((et, data))

        ctrl = SocLimitController(hass, updater, calc)
        ctrl.set_enabled(True)
        ctrl.process()              # spawns Stop; blocks on gate after recording token
        await asyncio.sleep(0)      # let _stop record _pending then block on send
        # The stop took effect at the charger before its HTTP response returned:
        updater.data = {"state": 1, "sessionEnergy": 0.0, "evseEnabled": 1, "suspendLimits": 0}
        ctrl.process()              # confirms via the in-flight token
        gate.set()
        await asyncio.sleep(0.02)
        assert len(events) == 1

    asyncio.run(scenario())


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
        updater.data = {"state": 4, "sessionEnergy": 30.0, "suspendLimits": 0}
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
