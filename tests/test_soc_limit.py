import asyncio
from unittest.mock import AsyncMock, MagicMock

from conftest import SnapshotBackedMock as _MockUpdater
from conftest import snapshot_of as _snapshot_of
from custom_components.eveus.ev_sensors import CachedSOCCalculator
from custom_components.eveus.snapshot import EveusSnapshot
from custom_components.eveus.soc_limit import EVENT_SOC_LIMIT_REACHED, SocLimitController


class _SnapshotFromData:
    """Mixin for the hand-written doubles below: `snapshot` follows `data`."""

    @property
    def snapshot(self) -> EveusSnapshot:
        return _snapshot_of(self)


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
    u = _MockUpdater()
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
    updater.send_command.assert_awaited_once()
    assert updater.send_command.await_args.args == ("evseEnabled", 1)


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
    # Pin the literal event type: the constant is not a public HA contract on
    # its own, but the string is — automations key off it by name.
    assert etype == "eveus_soc_limit_reached" == EVENT_SOC_LIMIT_REACHED
    assert data["device_number"] == 1
    assert data["soc"] == 80
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
    assert scheduled == []
    assert events == []


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
    assert len(scheduled) == 1
    assert len(events) == 1


def test_does_not_fire_when_disabled():
    ctrl, scheduled, events = _make(_calc(), _updater(session_energy=30.0))
    ctrl.set_enabled(False)
    ctrl.process()
    assert scheduled == []
    assert events == []


def test_disable_all_limits_stands_down_then_resumes():
    updater = _updater(state=4, session_energy=30.0, ev=0)
    updater.data["suspendLimits"] = 1
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=20, cap=50, corr=0), updater
    )
    ctrl.set_enabled(True)
    ctrl.process()
    assert scheduled == []
    assert events == []

    updater.data["suspendLimits"] = 0
    ctrl.process()
    _confirm(updater)
    ctrl.process()
    assert len(scheduled) == 1
    assert len(events) == 1


def test_does_not_fire_below_target():
    ctrl, scheduled, events = _make(_calc(target=90), _updater(session_energy=30.0))
    ctrl.set_enabled(True)
    ctrl.process()
    assert scheduled == []
    assert events == []


def test_ignored_on_failed_poll():
    ctrl, scheduled, events = _make(_calc(), _updater(session_energy=30.0, ok=False))
    ctrl.set_enabled(True)
    ctrl.process()
    assert scheduled == []
    assert events == []


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
    assert len(scheduled) == 2
    assert len(events) == 2


def test_confirmation_during_inflight_stop_is_not_lost():
    # F9: a poll observing evseEnabled==1 while the Stop POST is still in flight
    # must still confirm — the attempt is recorded before the await, so the
    # boundary re-arm cannot cancel it and lose the event.
    async def scenario():
        calc = _calc(target=80, initial=20, cap=50, corr=0)
        gate = asyncio.Event()
        events = []

        async def slow_send(_cmd, _val, **_kwargs):
            await gate.wait()
            return True

        updater = _MockUpdater()
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
        stop_task = ctrl._stop_task
        await asyncio.sleep(0)      # let _stop record _pending then block on send
        # The stop took effect at the charger before its HTTP response returned:
        updater.data = {"state": 1, "sessionEnergy": 0.0, "evseEnabled": 1, "suspendLimits": 0}
        ctrl.process()              # confirms via the in-flight token
        gate.set()
        await asyncio.gather(stop_task, return_exceptions=True)
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

        async def slow_send(_cmd, _val, **_kwargs):
            await gate.wait()
            return True

        updater = _MockUpdater()
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
        ctrl.process()              # spawns attempt A, which blocks on the gate
        attempt_a = ctrl._stop_task
        assert attempt_a is not None  # without evseEnabled no attempt spawns
        await asyncio.sleep(0)      # let A reach `await gate.wait()`
        ctrl.set_enabled(False)     # cancels A, bumps the generation
        ctrl.set_enabled(True)      # re-arm into a fresh generation
        gate.set()                  # release A (now superseded/cancelled)
        await asyncio.gather(attempt_a, return_exceptions=True)
        assert attempt_a.cancelled()
        assert events == []         # the superseded attempt fired nothing
        assert ctrl._fired is False  # new epoch's latch is clean

    asyncio.run(scenario())


def test_new_controller_starts_disabled_and_does_not_enforce():
    """A freshly constructed controller must not enforce until set_enabled(True)
    is called explicitly — process() must be a no-op on a bare instance."""
    updater = _updater(ev=0)
    ctrl, _scheduled, events = _make(_calc(), updater)

    assert ctrl.enabled is False
    ctrl.process()
    assert events == []
    updater.send_command.assert_not_called()


def test_new_controller_initial_state_is_clean():
    """Construction-time invariant: nothing pending/latched before set_enabled
    ever runs. Guards against a mis-initialized latch or token being masked by
    the fact that _rearm() always resets this same state on first enable."""
    ctrl, _scheduled, _events = _make(_calc(), _updater())
    assert ctrl._fired is False
    assert ctrl._pending is None
    assert ctrl._pending_energy is None
    assert ctrl._pending_session_time is None
    assert ctrl._generation == 0
    assert ctrl._stop_task is None


def test_small_energy_drop_within_reset_epsilon_discards_stale_token():
    # A drop just over the real reset epsilon (well under a much looser one)
    # must still be treated as a new session and discard the stale pending
    # token, so an unrelated evseEnabled=1 later doesn't falsely confirm the
    # old session's Stop.
    updater = _updater(state=4, session_energy=30.0, ev=0)
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=20, cap=50, corr=0), updater
    )
    ctrl.set_enabled(True)
    ctrl.process()  # Stop sent at 30.0 kWh; pending recorded
    # 0.7 kWh drop: above the real epsilon, so this must count as a reset.
    updater.data = {**updater.data, "sessionEnergy": 29.3, "evseEnabled": 0}
    ctrl.process()
    updater.data = {**updater.data, "evseEnabled": 1}  # unrelated stop, new "session"
    ctrl.process()
    assert events == []


def test_energy_drop_exactly_at_epsilon_boundary_is_not_a_reset():
    # The reset check is a strict less-than: a drop of EXACTLY the epsilon must
    # NOT be treated as a new session, so the same-session confirm still fires.
    updater = _updater(state=4, session_energy=30.0, ev=0)
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=20, cap=50, corr=0), updater
    )
    ctrl.set_enabled(True)
    ctrl.process()  # pending recorded at 30.0 kWh
    updater.data = {**updater.data, "sessionEnergy": 29.5}  # drop of exactly 0.5 kWh
    ctrl.process()
    updater.data = {**updater.data, "evseEnabled": 1}
    ctrl.process()
    assert len(events) == 1


def test_session_time_reset_discards_stale_token_even_when_energy_barely_moves():
    # A session boundary can also show up as sessionTime resetting even when
    # sessionEnergy drops by less than its own reset epsilon (so the energy
    # check alone would not catch it). sessionTime must be tracked and checked
    # independently of sessionEnergy. The 0.3 kWh nudge here is deliberately
    # sub-epsilon (< 0.5) so it can't masquerade as an energy-based reset, and
    # it also drops current SOC just below target so the rearm doesn't
    # immediately re-arm a fresh (legitimately confirmable) attempt.
    updater = _updater(state=4, session_energy=30.0, ev=0)
    updater.data["sessionTime"] = 5000
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=20, cap=50, corr=0), updater
    )
    ctrl.set_enabled(True)
    ctrl.process()  # Stop sent; pending recorded with sessionTime=5000
    updater.data = {**updater.data, "sessionEnergy": 29.7, "sessionTime": 100}
    ctrl.process()  # must detect the time reset and discard the stale token
    updater.data = {**updater.data, "evseEnabled": 1}  # unrelated stop, new session
    ctrl.process()
    assert events == []


def test_target_of_one_percent_is_a_valid_stop_point():
    # Only target <= 0 is meaningless; target == 1 is a real, enforceable value.
    ctrl, scheduled, events = _make(
        _calc(target=1, initial=20, cap=50, corr=0), _updater(session_energy=30.0)
    )
    ctrl.set_enabled(True)
    ctrl.process()
    assert len(scheduled) == 1


def test_zero_session_energy_is_a_valid_reading_at_high_initial_soc():
    # sessionEnergy == 0 is a legitimate reading (start of session), not a
    # value to be rejected by the sanity bound.
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=90, cap=50, corr=0), _updater(session_energy=0.0)
    )
    ctrl.set_enabled(True)
    ctrl.process()
    assert len(scheduled) == 1


def test_energy_exactly_at_max_bound_is_still_a_valid_reading():
    # The upper sanity bound is inclusive: energy == MAX_ENERGY_KWH must still
    # be accepted, not rejected as out-of-range.
    from custom_components.eveus.const import MAX_ENERGY_KWH

    ctrl, scheduled, events = _make(
        _calc(target=80, initial=0, cap=50, corr=0),
        _updater(session_energy=float(MAX_ENERGY_KWH)),
    )
    ctrl.set_enabled(True)
    ctrl.process()
    assert len(scheduled) == 1


def test_missing_session_energy_at_active_poll_does_not_crash_or_stop():
    updater = _updater(state=4, ev=0)
    del updater.data["sessionEnergy"]
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=20, cap=50, corr=0), updater
    )
    ctrl.set_enabled(True)
    ctrl.process()  # must not raise, must not schedule a stop
    assert scheduled == []
    assert events == []


def test_generation_increments_by_one_per_rearm():
    ctrl, scheduled, events = _make(_calc(), _updater())
    ctrl.set_enabled(True)  # rearm #1: 0 -> 1
    assert ctrl._generation == 1
    ctrl.set_enabled(False)  # rearm #2: 1 -> 2
    assert ctrl._generation == 2


def test_rearm_clears_pending_energy_and_session_time():
    updater = _updater(state=4, session_energy=30.0, ev=0)
    updater.data["sessionTime"] = 5000
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=20, cap=50, corr=0), updater
    )
    ctrl.set_enabled(True)
    ctrl.process()  # records pending_energy=30.0, pending_session_time=5000
    ctrl.set_enabled(False)  # rearm
    assert ctrl._pending_energy is None
    assert ctrl._pending_session_time is None


def test_session_end_without_evse_field_clears_pending_even_at_same_energy():
    # F6/F7 variant: the session-end boundary (state alone, no evseEnabled) must
    # discard a stale pending token even when the next session happens to start
    # at the SAME energy reading (so the separate energy-reset guard can't be
    # relied on to save this case).
    updater = _updater(state=4, session_energy=30.0, ev=0)
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=20, cap=50, corr=0), updater
    )
    ctrl.set_enabled(True)
    ctrl.process()  # Stop sent; pending recorded at 30.0 kWh
    updater.data = {"state": 1, "sessionEnergy": 30.0, "suspendLimits": 0}  # session end
    ctrl.process()  # boundary must discard the token
    updater.data = {"state": 4, "sessionEnergy": 30.0, "evseEnabled": 1, "suspendLimits": 0}
    ctrl.process()  # unrelated confirm in the new session
    assert events == []


def test_inflight_stop_task_not_yet_started_still_counts_at_boundary():
    # A stop task can be created (self._stop_task set) before its coroutine
    # body has run even one tick (no evseEnabled/pending recorded yet). A
    # session-end boundary arriving in that narrow window must still cancel it.
    async def scenario():
        calc = _calc(target=80, initial=20, cap=50, corr=0)

        async def slow_send(_cmd, _val, **_kwargs):
            await asyncio.sleep(100)
            return True

        updater = _MockUpdater()
        updater.available = True
        updater.last_update_success = True
        updater.device_number = 1
        updater.data = {"state": 4, "sessionEnergy": 30.0, "evseEnabled": 0, "suspendLimits": 0}
        updater.send_command = slow_send

        hass = MagicMock()
        hass.async_create_task = lambda coro: asyncio.ensure_future(coro)
        hass.bus.async_fire = MagicMock()

        ctrl = SocLimitController(hass, updater, calc)
        ctrl.set_enabled(True)
        ctrl.process()  # spawns the task; its body has NOT run yet
        assert ctrl._pending is None
        assert ctrl._stop_task is not None
        updater.data = {"state": 1, "sessionEnergy": 0.0, "suspendLimits": 0}
        ctrl.process()  # boundary: only "stop_task is not None" is true
        assert ctrl._stop_task is None

    asyncio.run(scenario())


def test_second_poll_does_not_spawn_concurrent_stop_while_one_is_inflight():
    async def scenario():
        calc = _calc(target=80, initial=20, cap=50, corr=0)
        gate = asyncio.Event()
        send_calls = []

        async def slow_send(cmd, val, **_kwargs):
            send_calls.append((cmd, val))
            await gate.wait()
            return True

        updater = _MockUpdater()
        updater.available = True
        updater.last_update_success = True
        updater.device_number = 1
        updater.data = {"state": 4, "sessionEnergy": 30.0, "evseEnabled": 0, "suspendLimits": 0}
        updater.send_command = slow_send

        hass = MagicMock()
        hass.async_create_task = lambda coro: asyncio.ensure_future(coro)
        hass.bus.async_fire = MagicMock()

        ctrl = SocLimitController(hass, updater, calc)
        ctrl.set_enabled(True)
        ctrl.process()  # spawns attempt A
        attempt_a = ctrl._stop_task
        await asyncio.sleep(0)  # let A reach the send_command gate
        ctrl.process()  # must see the in-flight task and wait, not spawn B
        await asyncio.sleep(0)  # let B run to its own gate, if it was (wrongly) spawned
        assert len(send_calls) == 1
        gate.set()
        await asyncio.gather(attempt_a, return_exceptions=True)
        assert len(send_calls) == 1

    asyncio.run(scenario())


def test_missing_device_number_defaults_to_one_in_emitted_event():
    class _NoDeviceNumberUpdater(_SnapshotFromData):
        available = True
        last_update_success = True
        data = {"state": 4, "sessionEnergy": 30.0, "evseEnabled": 0, "suspendLimits": 0}

        async def send_command(self, cmd, val, **_kwargs):
            return True

    updater = _NoDeviceNumberUpdater()
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=20, cap=50, corr=0), updater
    )
    ctrl.set_enabled(True)
    ctrl.process()
    _confirm(updater)
    ctrl.process()
    assert len(events) == 1
    assert events[0][1]["device_number"] == 1


def test_failed_stop_pending_is_actually_cleared_not_left_stale():
    # RC-001 companion: a rejected (non-exception) Stop must clear the pending
    # token, not merely "not emit yet" — otherwise a later unrelated
    # evseEnabled=1 would falsely confirm this failed attempt.
    updater = _updater(state=4, session_energy=30.0, stop_ok=False)
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=20, cap=50, corr=0), updater
    )
    ctrl.set_enabled(True)
    ctrl.process()
    assert ctrl._pending is None
    assert ctrl._pending_energy is None
    assert ctrl._pending_session_time is None


def test_command_exception_clears_pending_so_later_stop_is_not_misattributed():
    updater = _updater(state=4, session_energy=30.0, ev=0)
    updater.send_command = AsyncMock(side_effect=RuntimeError("boom"))
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=20, cap=50, corr=0), updater
    )
    ctrl.set_enabled(True)
    ctrl.process()  # send_command raises -> must be treated as a failed stop
    assert ctrl._pending is None
    assert events == []
    updater.data = {**updater.data, "evseEnabled": 1}  # unrelated stop, later
    ctrl.process()
    assert events == []


def test_auth_failure_clears_pending_and_starts_reauth():
    from homeassistant.exceptions import ConfigEntryAuthFailed

    updater = _updater(state=4, session_energy=30.0, ev=0)
    updater.send_command = AsyncMock(side_effect=ConfigEntryAuthFailed("bad creds"))
    updater.config_entry = MagicMock()
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=20, cap=50, corr=0), updater
    )
    ctrl.set_enabled(True)
    ctrl.process()
    assert ctrl._pending is None
    assert ctrl._pending_energy is None
    assert ctrl._pending_session_time is None
    updater.config_entry.async_start_reauth.assert_called_once_with(ctrl._hass)
    updater.data = {**updater.data, "evseEnabled": 1}
    ctrl.process()
    assert events == []


def test_auth_failure_after_generation_bump_does_not_clobber_new_generations_pending():
    # If this attempt's generation was superseded (a re-arm happened) by the
    # time the auth-failure handler runs, it must not touch _pending/_energy/
    # _session_time -- those now belong to the newer generation's own attempt.
    from homeassistant.exceptions import ConfigEntryAuthFailed

    updater = _updater(state=4, session_energy=30.0, ev=0)
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=20, cap=50, corr=0), updater
    )
    updater.config_entry = MagicMock()

    async def _bump_generation_then_fail(cmd, val, **_kwargs):
        ctrl._generation += 1
        ctrl._pending = ("new-token", 99)
        ctrl._pending_energy = 99.0
        ctrl._pending_session_time = 99.0
        raise ConfigEntryAuthFailed("bad creds")

    updater.send_command = _bump_generation_then_fail
    ctrl.set_enabled(True)
    ctrl.process()

    assert ctrl._pending == ("new-token", 99)
    assert ctrl._pending_energy == 99.0
    assert ctrl._pending_session_time == 99.0
    updater.config_entry.async_start_reauth.assert_called_once_with(ctrl._hass)


def test_suspendlimits_enabled_mid_flight_clears_pending_token():
    # process() reads suspendLimits=0 and schedules _stop(); by the time _stop()
    # re-reads the latest poll (its own stand-down re-check), suspendLimits has
    # flipped to 1 — the aborted attempt must not leave a stale pending token.
    # process() consults the snapshot once; _stop() consults it again, so the
    # flip belongs on the SECOND read.
    class _TogglingUpdater:
        available = True
        last_update_success = True
        device_number = 1
        data = {"state": 4, "sessionEnergy": 30.0, "evseEnabled": 0, "suspendLimits": 0}

        def __init__(self):
            self._reads = 0

        @property
        def snapshot(self):
            self._reads += 1
            suspend = 0 if self._reads < 2 else 1
            return EveusSnapshot.parse(
                {**self.data, "suspendLimits": suspend}, None
            )

        async def send_command(self, cmd, val, **_kwargs):
            return True

    updater = _TogglingUpdater()
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=20, cap=50, corr=0), updater
    )
    ctrl.set_enabled(True)
    ctrl.process()
    assert ctrl._pending is None
    assert ctrl._pending_energy is None
    assert ctrl._pending_session_time is None


def test_async_shutdown_disables_and_cancels_inflight_stop():
    """V-01: unload-time shutdown stops enforcement and cancels the stop task so
    no Stop command can reach the charger after the entry is gone."""
    async def scenario():
        ctrl = SocLimitController(MagicMock(), MagicMock(), _calc())
        ctrl._enabled = True

        async def _never():
            await asyncio.sleep(100)

        task = asyncio.ensure_future(_never())
        ctrl._stop_task = task
        await ctrl.async_shutdown()
        assert ctrl.enabled is False
        assert task.cancelled()

    asyncio.run(scenario())


import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


def _threshold_entity(data):
    import custom_components.eveus.number as number_mod
    updater = _MockUpdater()
    updater.available = True
    updater.data = data
    updater.send_command = AsyncMock(return_value=True)
    updater.config_entry = MagicMock()
    ent = number_mod.EveusUndervoltageThresholdNumber(
        updater, number_mod.UNDERVOLTAGE_THRESHOLD_NUMBER, device_number=1
    )
    ent.hass = MagicMock()
    ent.async_write_ha_state = MagicMock()
    return ent


def test_v02_negative_minvoltage_keeps_static_floor():
    ent = _threshold_entity({"aiVoltage": 215, "minVoltage": -1000})
    assert ent.native_min_value == 210


def test_v02_offlist_minvoltage_keeps_static_floor():
    ent = _threshold_entity({"aiVoltage": 215, "minVoltage": 190})
    assert ent.native_min_value == 210


def test_v02_supported_minvoltage_still_tracks():
    ent = _threshold_entity({"aiVoltage": 215, "minVoltage": 180})
    assert ent.native_min_value == 190


@pytest.mark.parametrize("suspend", [None, "bad", 2, -1])
def test_v03_does_not_enforce_when_suspendlimits_unknown(suspend):
    updater = _MockUpdater()
    updater.available = True
    updater.last_update_success = True
    updater.device_number = 1
    base = {"state": 4, "sessionEnergy": 30.0, "evseEnabled": 0, "suspendLimits": 0}
    if suspend is None:
        del base["suspendLimits"]
    else:
        base["suspendLimits"] = suspend
    updater.data = base
    updater.send_command = AsyncMock(return_value=True)

    hass = MagicMock()
    hass.async_create_task = lambda coro: asyncio.run(coro)
    hass.bus.async_fire = MagicMock()

    calc = _calc(target=80, initial=20, cap=50, corr=0)
    ctrl = SocLimitController(hass, updater, calc)
    ctrl.set_enabled(True)
    ctrl.process()
    updater.send_command.assert_not_called()


def test_v03_enforces_only_when_suspendlimits_zero():
    updater = _MockUpdater()
    updater.available = True
    updater.last_update_success = True
    updater.device_number = 1
    updater.data = {"state": 4, "sessionEnergy": 30.0, "evseEnabled": 0, "suspendLimits": 0}
    updater.send_command = AsyncMock(return_value=True)

    hass = MagicMock()
    hass.async_create_task = lambda coro: asyncio.run(coro)
    hass.bus.async_fire = MagicMock()

    calc = _calc(target=80, initial=20, cap=50, corr=0)
    ctrl = SocLimitController(hass, updater, calc)
    ctrl.set_enabled(True)
    ctrl.process()
    updater.send_command.assert_awaited_once()
    assert updater.send_command.await_args.args == ("evseEnabled", 1)


def test_v17_malformed_suspendlimits_does_not_retrigger_switch_off():
    from custom_components.eveus.switch import EveusSocLimitSwitch

    controller = MagicMock()
    updater = MagicMock()
    updater.config_entry = MagicMock()
    sw = EveusSocLimitSwitch(updater, controller, device_number=1)
    sw.hass = MagicMock()
    sw.async_write_ha_state = MagicMock()

    sw._updater.data = {"suspendLimits": 1}
    sw._handle_coordinator_update()              # baseline: master suspended
    asyncio.run(sw.async_turn_on())             # re-enable while suspended
    assert sw.is_on is True

    sw._updater.data = {}                         # malformed poll: no suspendLimits
    sw._handle_coordinator_update()
    sw._updater.data = {"suspendLimits": 1}       # unchanged master, valid again
    sw._handle_coordinator_update()
    assert sw.is_on is True                       # NOT flipped off a second time


# --- Stop eligibility is re-checked at the moment of transmission ---


class _RecordingResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return None

    def raise_for_status(self) -> None:
        if self.status >= 400:
            import aiohttp

            raise aiohttp.ClientResponseError(request_info=None, history=(), status=self.status)


class _RecordingSession:
    def __init__(self, *statuses: int) -> None:
        self.statuses = list(statuses) or [200]
        self.calls: list[dict] = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return _RecordingResponse(status)


def _seed(updater, payload: dict) -> None:
    """Publish a payload on a REAL coordinator, snapshot included.

    `_async_update_data` parses the snapshot as it publishes `data`; a test
    that assigns `data` by hand has to do both, or the controller reads a
    snapshot from the previous payload.
    """
    updater.data = payload
    updater._snapshot = EveusSnapshot.parse(payload, None)


def _real_stack(monkeypatch, session):
    """A real EveusUpdater + CommandManager under a SOC controller, loop-scheduled."""
    from custom_components.eveus import common_network
    from custom_components.eveus.common_network import EveusUpdater

    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater("192.168.1.50", "u", "p", MagicMock(loop=None))  # NOSONAR(python:S1313) - RFC 1918 test fixture
    updater._device_available = True
    updater.last_update_success = True
    updater._schedule_post_command_refresh = lambda: None
    _seed(updater, {"state": 4, "sessionEnergy": 30.0, "evseEnabled": 0, "suspendLimits": 0})
    hass = MagicMock()
    events: list = []
    hass.async_create_task = lambda coro: asyncio.get_running_loop().create_task(coro)
    hass.bus.async_fire = lambda etype, data=None: events.append((etype, data))
    ctrl = SocLimitController(hass, updater, _calc(target=80, initial=20, cap=50))
    ctrl.set_enabled(True)
    return updater, ctrl, events


async def _settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


def _stop_posts(session) -> list:
    return [c for c in session.calls if "evseEnabled" in str(c.get("data"))]


def test_stop_queued_behind_the_command_lock_is_dropped_when_limits_are_suspended(
    monkeypatch,
) -> None:
    session = _RecordingSession(200)
    updater, ctrl, events = _real_stack(monkeypatch, session)

    async def scenario() -> None:
        lock = updater._command_manager._lock
        await lock.acquire()
        ctrl.process()
        await _settle()
        # A poll lands while the Stop waits its turn: "Disable limits" is on.
        _seed(updater, {**updater.data, "suspendLimits": 1})
        ctrl.process()
        lock.release()
        await ctrl._stop_task

    asyncio.run(scenario())

    assert _stop_posts(session) == []
    assert events == []
    assert updater._command_manager.consecutive_failures == 0


def test_stop_retry_after_backoff_is_dropped_when_limits_are_suspended(monkeypatch) -> None:
    session = _RecordingSession(503, 200)
    updater, ctrl, events = _real_stack(monkeypatch, session)

    async def scenario() -> None:
        in_backoff = asyncio.Event()
        release = asyncio.Event()

        async def controlled_backoff(attempt: int) -> None:
            in_backoff.set()
            await release.wait()

        updater._command_manager._sleep_backoff = controlled_backoff
        ctrl.process()
        await asyncio.wait_for(in_backoff.wait(), 2)
        _seed(updater, {**updater.data, "suspendLimits": 1})
        release.set()
        await ctrl._stop_task

    asyncio.run(scenario())

    assert len(_stop_posts(session)) == 1  # the first attempt only, no retry
    assert events == []


def test_stop_queued_behind_the_command_lock_is_dropped_after_a_rearm(monkeypatch) -> None:
    """A re-arm cancels the task; if cancellation loses the race with the lock
    hand-off, the generation check at transmission still refuses to POST."""
    session = _RecordingSession(200)
    updater, ctrl, events = _real_stack(monkeypatch, session)

    async def scenario() -> None:
        ctrl.process()
        stale_generation_task = ctrl._stop_task
        # Simulate the lost race: bump the generation without cancelling.
        ctrl._generation += 1
        ctrl._stop_task = None
        await stale_generation_task

    asyncio.run(scenario())

    assert _stop_posts(session) == []
    assert events == []


def test_eligible_stop_posts_once_and_waits_for_confirmation(monkeypatch) -> None:
    session = _RecordingSession(200)
    updater, ctrl, events = _real_stack(monkeypatch, session)

    async def scenario() -> None:
        ctrl.process()
        await ctrl._stop_task
        assert events == []
        _seed(updater, {**updater.data, "evseEnabled": 1})
        ctrl.process()

    asyncio.run(scenario())

    assert len(_stop_posts(session)) == 1
    assert [etype for etype, _ in events] == [EVENT_SOC_LIMIT_REACHED]


# --- Snapshot migration (P2.2) ---------------------------------------------


def test_controller_evaluates_the_updater_snapshot():
    """The SOC limit is the one stop Home Assistant enforces itself, so it must
    read the same parsed values every other consumer reads — not its own
    conversion of the raw payload."""
    from custom_components.eveus.snapshot import EveusSnapshot

    class _SnapshotOnlyUpdater:
        available = True
        last_update_success = True
        device_number = 1
        data = None
        snapshot = EveusSnapshot.parse(
            {
                "state": 4,
                "currentSet": 16,
                "sessionEnergy": 30.0,
                "evseEnabled": 0,
                "suspendLimits": 0,
            },
            None,
        )

        def __init__(self):
            self.sent = []

        async def send_command(self, cmd, val, **_kwargs):
            self.sent.append((cmd, val))
            return True

    updater = _SnapshotOnlyUpdater()
    ctrl, _scheduled, _events = _make(
        _calc(target=80, initial=20, cap=50, corr=0), updater
    )
    ctrl.set_enabled(True)
    ctrl.process()

    assert updater.sent == [("evseEnabled", 1)]


# --- Entry guard (F1) --------------------------------------------------------
# `not available or not last_update_success` has two operands and each carries a
# case the other cannot see. This is the one stop Home Assistant enforces itself,
# so a poll that either signal calls unhealthy must never reach the charger.


import pytest  # noqa: E402  (grouped with the F1 tests it serves)


@pytest.mark.parametrize(
    ("available", "last_update_success"),
    [
        # An unexpected exception in the poll fails the coordinator's refresh
        # without touching the charger-reachability flag.
        (True, False),
        # A refresh that hands back the held data (a second poll while one is
        # in flight) reports success while the outage flag is still set.
        (False, True),
    ],
)
def test_stands_down_unless_both_reachability_signals_are_healthy(
    available, last_update_success
):
    updater = _updater(state=4, session_energy=30.0, ev=0)
    updater.available = available
    updater.last_update_success = last_update_success
    ctrl, scheduled, events = _make(
        _calc(target=80, initial=20, cap=50, corr=0), updater
    )
    ctrl.set_enabled(True)
    ctrl.process()
    assert scheduled == []
    updater.send_command.assert_not_awaited()
    assert events == []
