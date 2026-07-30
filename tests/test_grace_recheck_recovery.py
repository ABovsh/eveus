"""Regression: recovery after a grace-recheck-driven unavailable must reach HA.

Live incident 2026-07-06: one failed poll expired the grace period via the
scheduled recheck; when the charger came back, the binary sensors' recovery
write was skipped because the recheck's direct async_write_ha_state() bypassed
WriteOnChangeMixin bookkeeping (_last_written_available stayed True), so
_write_if_changed saw "no change" and the entities stayed unavailable in HA
until restart.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from conftest import EveusTestUpdater

from custom_components.eveus import binary_sensor as binary_sensor_mod
from custom_components.eveus import common_base


def _car_connected_sensor(updater: EveusTestUpdater):
    description = next(
        item for item in binary_sensor_mod.BINARY_SENSORS if item.name == "Car Connected"
    )
    return binary_sensor_mod.EveusBinarySensor(updater, description, 1)


def test_recovery_after_grace_recheck_is_written_to_ha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater = EveusTestUpdater({"state": 2}, available=True)
    sensor = _car_connected_sensor(updater)
    sensor.hass = SimpleNamespace()

    writes: list[bool] = []
    sensor.async_write_ha_state = lambda: writes.append(sensor.available)

    scheduled: list = []
    monkeypatch.setattr(
        common_base,
        "async_call_later",
        lambda _hass, _delay, action: scheduled.append(action) or (lambda: None),
    )

    fake_monotonic = 1_000_000.0
    monkeypatch.setattr(common_base.time, "monotonic", lambda: fake_monotonic)

    # Healthy poll: available, car not connected.
    sensor._handle_coordinator_update()
    assert sensor.available is True

    # One failed poll starts the grace window and schedules a recheck.
    updater.available = False
    sensor._handle_coordinator_update()
    assert sensor.available is True
    assert scheduled, "grace recheck must be scheduled"

    # Grace expires; the scheduled recheck fires and writes unavailable.
    fake_monotonic += common_base.AVAILABILITY_GRACE_PERIOD + 10
    scheduled[-1](None)
    assert sensor.available is False
    assert writes and writes[-1] is False

    # Charger recovers on the next poll: HA MUST receive an available write.
    updater.available = True
    sensor._handle_coordinator_update()
    assert sensor.available is True
    assert writes[-1] is True, (
        "recovery after a recheck-driven unavailable was never written to HA"
    )


def test_grace_recheck_delay_math_and_expiry_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins the exact `_schedule_grace_recheck` delay math (`grace_period -
    unavailable_duration`, plus the fixed +0.5s buffer) and the strict `<`
    grace-expiry boundary, none of which the recovery-focused test above
    happens to distinguish."""
    updater = EveusTestUpdater({"state": 2}, available=True)
    sensor = _car_connected_sensor(updater)
    sensor.hass = SimpleNamespace()
    sensor.async_write_ha_state = lambda: None

    calls: list[tuple[float, object]] = []

    def _fake_call_later(_hass, delay, action):
        calls.append((delay, action))
        return lambda: None

    monkeypatch.setattr(common_base, "async_call_later", _fake_call_later)

    fake_monotonic = 1_000_000.0
    monkeypatch.setattr(common_base.time, "monotonic", lambda: fake_monotonic)

    sensor._handle_coordinator_update()
    assert sensor.available is True

    # First unavailable poll: reschedule delay = grace_period (full) + 0.5s.
    updater.available = False
    sensor._handle_coordinator_update()
    assert calls[-1][0] == common_base.AVAILABILITY_GRACE_PERIOD + 0.5

    # Still within grace, 10s later: reschedule delay must be the REMAINING
    # grace time (grace_period - elapsed), not grace_period + elapsed.
    fake_monotonic += 10
    sensor._handle_coordinator_update()
    assert sensor.available is True
    assert calls[-1][0] == (common_base.AVAILABILITY_GRACE_PERIOD - 10) + 0.5

    # Exactly at the grace boundary (duration == grace_period): must already
    # be expired (strict '<', not '<=').
    fake_monotonic += (common_base.AVAILABILITY_GRACE_PERIOD - 10)
    sensor._handle_coordinator_update()
    assert sensor.available is False


def test_unavailable_transition_log_gate_is_and_not_or(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`if self._last_known_available and self._should_log_availability():`
    must be a real AND: once a transition to unavailable has already been
    logged (`_last_known_available` flips to False), a later still-unavailable
    poll must NOT log again even once the rate limiter would allow it."""
    import custom_components.eveus.common_base as common_base_mod

    updater = EveusTestUpdater({"state": 2}, available=True)
    sensor = _car_connected_sensor(updater)
    sensor.hass = SimpleNamespace()
    sensor.async_write_ha_state = lambda: None

    monkeypatch.setattr(common_base, "async_call_later", lambda *a, **k: (lambda: None))

    fake_monotonic = 1_000_000.0
    monkeypatch.setattr(common_base.time, "monotonic", lambda: fake_monotonic)

    captured: list[tuple] = []
    orig_debug = common_base_mod._LOGGER.debug

    def _capture(msg, *args, **kwargs):
        if "unavailable after grace period" in msg:
            captured.append(args)

    common_base_mod._LOGGER.debug = _capture
    try:
        sensor._handle_coordinator_update()
        updater.available = False
        sensor._handle_coordinator_update()  # starts the grace window

        fake_monotonic += common_base.AVAILABILITY_GRACE_PERIOD + 10
        sensor._handle_coordinator_update()  # grace expires: first "unavailable" log
        assert sensor._last_known_available is False
        assert len(captured) == 1

        # Long past the rate-limit window, still unavailable: a real
        # transition already happened, so this must stay quiet.
        fake_monotonic += 10_000
        sensor._handle_coordinator_update()
    finally:
        common_base_mod._LOGGER.debug = orig_debug

    assert len(captured) == 1, (
        "repeat unavailable polls after the first transition must not re-log"
    )


def test_last_known_available_flips_to_exact_false_at_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`self._last_known_available = False` must set the exact boolean False
    (not True or None) once the grace period truly expires."""
    updater = EveusTestUpdater({"state": 2}, available=True)
    sensor = _car_connected_sensor(updater)
    sensor.hass = SimpleNamespace()
    sensor.async_write_ha_state = lambda: None
    monkeypatch.setattr(common_base, "async_call_later", lambda *a, **k: (lambda: None))

    fake_monotonic = 1_000_000.0
    monkeypatch.setattr(common_base.time, "monotonic", lambda: fake_monotonic)

    sensor._handle_coordinator_update()
    updater.available = False
    sensor._handle_coordinator_update()
    fake_monotonic += common_base.AVAILABILITY_GRACE_PERIOD + 10
    sensor._handle_coordinator_update()

    assert sensor._last_known_available is False


def test_grace_recheck_unsub_is_exactly_none_after_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_cancel_grace_recheck` must reset the handle to exactly None (not a
    truthy-but-wrong placeholder), since callers use `is not None` to decide
    whether a recheck is pending."""
    updater = EveusTestUpdater({"state": 2}, available=True)
    sensor = _car_connected_sensor(updater)
    sensor.hass = SimpleNamespace()
    sensor.async_write_ha_state = lambda: None
    monkeypatch.setattr(common_base, "async_call_later", lambda *a, **k: (lambda: None))

    fake_monotonic = 1_000_000.0
    monkeypatch.setattr(common_base.time, "monotonic", lambda: fake_monotonic)

    sensor._handle_coordinator_update()
    updater.available = False
    sensor._handle_coordinator_update()
    assert sensor._grace_recheck_unsub is not None

    sensor._cancel_grace_recheck()
    assert sensor._grace_recheck_unsub is None


def test_default_label_reaches_the_log_call_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_update_availability_state`'s `label` default ("Entity") is only ever
    exercised through this un-overridden default path (every other caller in
    the suite passes an explicit label); pin the exact value passed to the
    log call's %s argument."""
    import custom_components.eveus.common_base as common_base_mod

    updater = EveusTestUpdater({"state": 2}, available=True)
    sensor = _car_connected_sensor(updater)
    sensor.hass = SimpleNamespace()
    sensor.async_write_ha_state = lambda: None
    monkeypatch.setattr(common_base, "async_call_later", lambda *a, **k: (lambda: None))

    fake_monotonic = 1_000_000.0
    monkeypatch.setattr(common_base.time, "monotonic", lambda: fake_monotonic)

    captured: list[tuple] = []
    orig_debug = common_base_mod._LOGGER.debug

    def _capture(msg, *args, **kwargs):
        captured.append(args)

    common_base_mod._LOGGER.debug = _capture
    try:
        sensor._handle_coordinator_update()
        updater.available = False
        sensor._handle_coordinator_update()
        fake_monotonic += common_base.AVAILABILITY_GRACE_PERIOD + 10
        # Calling the base method directly (no label kwarg) exercises the
        # signature's default, distinct from _handle_coordinator_update which
        # also doesn't pass one - both hit the same default.
        sensor._update_availability_state()
    finally:
        common_base_mod._LOGGER.debug = orig_debug

    assert captured, "expected an 'unavailable after grace period' debug log"
    assert captured[-1][0] == "Entity"


def test_control_entity_label_default_reaches_the_log_call_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ControlEntityMixin._control_entity_label`'s own default ("Entity") is
    only used by a bare mixin instance - every real control subclass overrides
    it - so nothing else in the suite pins its exact value."""
    from custom_components.eveus.common_base import BaseEveusEntity, ControlEntityMixin
    import custom_components.eveus.common_base as common_base_mod

    class _Control(ControlEntityMixin, BaseEveusEntity):
        ENTITY_NAME = "Probe Control Label"

    updater = EveusTestUpdater({}, available=True)
    entity = _Control(updater, 1)
    entity.hass = SimpleNamespace()
    entity.async_write_ha_state = lambda: None
    monkeypatch.setattr(common_base, "async_call_later", lambda *a, **k: (lambda: None))

    fake_monotonic = 1_000_000.0
    monkeypatch.setattr(common_base.time, "monotonic", lambda: fake_monotonic)

    captured: list[tuple] = []
    orig_debug = common_base_mod._LOGGER.debug

    def _capture(msg, *args, **kwargs):
        captured.append(args)

    common_base_mod._LOGGER.debug = _capture
    try:
        entity._update_availability_state()
        updater.available = False
        entity._update_availability_state()
        fake_monotonic += common_base.CONTROL_GRACE_PERIOD + 10
        entity._update_availability_state()
    finally:
        common_base_mod._LOGGER.debug = orig_debug

    assert captured, "expected an 'unavailable after grace period' debug log"
    assert captured[-1][0] == "Entity"


def test_clear_optimistic_state_default_is_false() -> None:
    """`_update_availability_state`'s `clear_optimistic_state` default (False)
    must NOT clear optimistic state at grace expiry unless a caller
    explicitly opts in - only ControlEntityMixin does that, always passing
    True explicitly, so nothing else in the suite exercises the default."""
    from custom_components.eveus.common_base import BaseEveusEntity, OptimisticControlMixin

    class _Probe(OptimisticControlMixin, BaseEveusEntity):
        ENTITY_NAME = "Probe Optimistic Default"

    updater = EveusTestUpdater({}, available=True)
    entity = _Probe(updater, 1)
    entity._init_optimistic_control()
    entity._set_optimistic_value(42)
    entity.hass = None  # _schedule_grace_recheck no-ops without hass

    entity._update_availability_state()
    updater.available = False
    entity._update_availability_state()
    # Force straight past the grace period via direct monotonic control.
    entity._unavailable_since = -10_000.0
    entity._update_availability_state()

    assert entity.available is False
    assert entity._optimistic_value == 42, (
        "clear_optimistic_state must default to False: the optimistic value "
        "must survive a grace-period expiry unless explicitly cleared"
    )
