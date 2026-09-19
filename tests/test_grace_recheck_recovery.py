"""Regression: recovery after a grace-expiry-driven unavailable must reach HA.

The grace window now closes on the coordinator's wake-up, which re-runs every
listener's `_handle_coordinator_update`; these tests drive that call directly.

Live incident 2026-07-06: one failed poll expired the grace period via the
(then per-entity) scheduled recheck; when the charger came back, the binary sensors' recovery
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


def test_recheck_write_routes_through_write_on_change_bookkeeping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The grace-expiry write must go through the entity's own write path,
    not a raw `async_write_ha_state()`. The binary sensor above blanks its value
    while unavailable, so its recovery write goes through on the value change
    alone and can't tell the two apart; an entity whose value is unchanged
    across the outage can — a raw write leaves `_last_written_available` True
    and `_write_if_changed` then suppresses recovery as "unchanged".
    """
    from custom_components.eveus.common_base import BaseEveusEntity, WriteOnChangeMixin

    class _Probe(WriteOnChangeMixin, BaseEveusEntity):
        ENTITY_NAME = "Probe Constant Value"

        def _handle_coordinator_update(self) -> None:
            self._update_availability_state()
            self._write_if_changed("constant")

    updater = EveusTestUpdater({}, available=True)
    entity = _Probe(updater, 1)
    entity._init_write_on_change()
    entity.hass = SimpleNamespace()

    writes: list[bool] = []
    entity.async_write_ha_state = lambda: writes.append(entity.available)

    fake_monotonic = 1_000_000.0
    monkeypatch.setattr(common_base.time, "monotonic", lambda: fake_monotonic)

    entity._handle_coordinator_update()
    assert writes == [True]

    updater.available = False
    entity._handle_coordinator_update()  # starts the grace window

    fake_monotonic += common_base.AVAILABILITY_GRACE_PERIOD + 10
    entity._handle_coordinator_update()  # the coordinator's wake-up
    assert entity.available is False
    assert entity._last_written_available is False, (
        "the recheck bypassed WriteOnChangeMixin bookkeeping"
    )

    updater.available = True
    entity._handle_coordinator_update()
    assert writes[-1] is True, "recovery write was suppressed as unchanged"


def test_grace_expiry_boundary_is_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exactly at the grace boundary (duration == grace_period) the entity is
    already gone: strict '<', not '<='."""
    updater = EveusTestUpdater({"state": 2}, available=True)
    sensor = _car_connected_sensor(updater)
    sensor.hass = SimpleNamespace()
    sensor.async_write_ha_state = lambda: None

    fake_monotonic = 1_000_000.0
    monkeypatch.setattr(common_base.time, "monotonic", lambda: fake_monotonic)

    sensor._handle_coordinator_update()
    updater.available = False
    sensor._handle_coordinator_update()
    fake_monotonic += 10
    sensor._handle_coordinator_update()
    assert sensor.available is True

    fake_monotonic += common_base.AVAILABILITY_GRACE_PERIOD - 10
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

    fake_monotonic = 1_000_000.0
    monkeypatch.setattr(common_base.time, "monotonic", lambda: fake_monotonic)

    sensor._handle_coordinator_update()
    updater.available = False
    sensor._handle_coordinator_update()
    fake_monotonic += common_base.AVAILABILITY_GRACE_PERIOD + 10
    sensor._handle_coordinator_update()

    assert sensor._last_known_available is False


def test_default_label_reaches_the_log_call_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`BaseEveusEntity._availability_label` ("Entity") is what a non-control
    entity logs; pin the exact value passed to the log call's %s argument."""
    import custom_components.eveus.common_base as common_base_mod

    updater = EveusTestUpdater({"state": 2}, available=True)
    sensor = _car_connected_sensor(updater)
    sensor.hass = SimpleNamespace()
    sensor.async_write_ha_state = lambda: None

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
    """`_clear_optimistic_on_unavailable` defaults to False: grace expiry must
    NOT clear optimistic state unless the class opts in - only
    ControlEntityMixin does, so nothing else in the suite exercises the default."""
    from custom_components.eveus.common_base import BaseEveusEntity, OptimisticControlMixin

    class _Probe(OptimisticControlMixin, BaseEveusEntity):
        ENTITY_NAME = "Probe Optimistic Default"

    updater = EveusTestUpdater({}, available=True)
    entity = _Probe(updater, 1)
    entity._init_optimistic_control()
    entity._set_optimistic_value(42)
    entity.hass = None

    entity._update_availability_state()
    updater.available = False
    entity._update_availability_state()
    updater.seconds_unavailable = 10_000.0
    entity._update_availability_state()

    assert entity.available is False
    assert entity._optimistic_value == 42, (
        "_clear_optimistic_on_unavailable must default to False: the optimistic value "
        "must survive a grace-period expiry unless explicitly cleared"
    )
