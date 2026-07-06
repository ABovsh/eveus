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
