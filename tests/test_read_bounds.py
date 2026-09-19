"""Bounds applied when reading values back: Last Session sensors and their
restore, schedule current and its attributes."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from custom_components.eveus.number import EveusSetpointNumber, SCHEDULE_LIMIT_NUMBERS
from custom_components.eveus.session_history import LastSessionEnergySensor
from conftest import SnapshotBackedMock
from custom_components.eveus.sensor_definitions import _make_schedule_attrs
from conftest import PayloadUpdater
import custom_components.eveus.session_history as session_history  # noqa: E402
from custom_components.eveus.const import FINISHED_REASONS  # noqa: E402
from test_soc_autofill import (  # noqa: E402
    _no_dispatcher,  # noqa: F401 - autouse fixture, applies by being imported here
    )


def _session_sensor() -> LastSessionEnergySensor:
    updater = MagicMock()
    updater.device_number = 1
    return LastSessionEnergySensor(updater, 1)


def _event(**data) -> SimpleNamespace:
    payload = {
        "device_number": 1,
        "reason": "complete",
        "session_energy_kwh": 18.46,
        "session_cost": 49.78,
        "session_duration_s": 22320,
    }
    payload.update(data)
    return SimpleNamespace(data=payload)


_SCHEDULE_CURRENT = next(
    d for d in SCHEDULE_LIMIT_NUMBERS if d.key == "schedule_1_current_limit"
)


def _schedule_number() -> tuple[EveusSetpointNumber, MagicMock]:
    updater = SnapshotBackedMock()
    updater.available = True
    updater.data = {"sh1CurrentValue": 6}
    updater.config_entry = MagicMock()
    ent = EveusSetpointNumber(updater, _SCHEDULE_CURRENT, device_number=1, max_value=16.0)
    ent.hass = MagicMock()
    ent.async_write_ha_state = MagicMock()
    return ent, updater


@pytest.mark.parametrize("bad", [-1.0, 2_000_000.0, True, "18.46", float("inf")])
def test_last_session_sensor_rejects_out_of_domain_event_values(bad) -> None:
    sensor = _session_sensor()
    sensor._handle_finished_event(_event(session_energy_kwh=bad))
    assert sensor.native_value is None


def test_last_session_sensor_still_captures_valid_value() -> None:
    sensor = _session_sensor()
    sensor._handle_finished_event(_event())
    assert sensor.native_value == pytest.approx(18.46)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_state", ["inf", "nan", "-3", "1e12"])
async def test_restore_rejects_non_finite_and_out_of_range(bad_state: str) -> None:
    sensor = _session_sensor()
    state = SimpleNamespace(state=bad_state, attributes={"reason": "complete"})
    await sensor._async_restore_state(state)
    assert sensor.native_value is None


@pytest.mark.asyncio
async def test_restore_keeps_valid_value() -> None:
    sensor = _session_sensor()
    state = SimpleNamespace(state="18.46", attributes={"reason": "complete"})
    await sensor._async_restore_state(state)
    assert sensor.native_value == pytest.approx(18.46)


def test_schedule_current_reads_sub_minimum_device_value() -> None:
    ent, _ = _schedule_number()
    assert ent._read_device_value() == pytest.approx(6.0)


def test_schedule_current_still_rejects_negative_and_over_max() -> None:
    ent, updater = _schedule_number()
    updater.data = {"sh1CurrentValue": -1}
    assert ent._read_device_value() is None
    updater.data = {"sh1CurrentValue": 99}
    assert ent._read_device_value() is None


@pytest.mark.asyncio
async def test_schedule_current_restores_sub_minimum_value() -> None:
    ent, updater = _schedule_number()
    updater.available = False
    updater.data = {}
    await ent._async_restore_state(SimpleNamespace(state="6", attributes={}))
    assert ent._attr_native_value == pytest.approx(6.0)

def test_schedule_attrs_show_sub_minimum_current() -> None:
    """Firmware reports sub-7A schedule setpoints verbatim; the sensor
    attribute must display them like the Number entity does."""
    updater = PayloadUpdater({
        "sh1Start": 60,
        "sh1Stop": 120,
        "sh1CurrentEnable": 1,
        "sh1CurrentValue": 6,
    })
    attrs = _make_schedule_attrs(1)(updater, None)
    assert attrs["current_limit_a"] == 6


def test_schedule_attrs_still_reject_negative_current() -> None:
    updater = PayloadUpdater({"sh1CurrentEnable": 1, "sh1CurrentValue": -3})
    attrs = _make_schedule_attrs(1)(updater, None)
    assert "current_limit_a" not in attrs

def _last_session_sensor():
    from unittest.mock import MagicMock

    updater = MagicMock()
    updater.device_number = 1
    sensor = session_history.LastSessionEnergySensor(updater, 1)
    sensor.hass = None
    return sensor


def _finished_event(reason):
    return SimpleNamespace(
        data={
            "device_number": 1,
            "reason": reason,
            "session_energy_kwh": 18.46,
        }
    )


def test_last_session_reason_rejects_a_value_outside_the_closed_set() -> None:
    """`_value_from_event` re-checks the numbers because the bus is public;
    the reason string gets the same treatment."""
    sensor = _last_session_sensor()
    sensor._handle_finished_event(_finished_event("x" * 5000))
    assert sensor.extra_state_attributes["reason"] is None


def test_last_session_reason_rejects_a_non_string() -> None:
    sensor = _last_session_sensor()
    sensor._handle_finished_event(_finished_event({"nested": "junk"}))
    assert sensor.extra_state_attributes["reason"] is None


def test_last_session_reason_keeps_every_reason_the_coordinator_fires() -> None:
    for reason in (*FINISHED_REASONS.values(), "stopped"):
        sensor = _last_session_sensor()
        sensor._handle_finished_event(_finished_event(reason))
        assert sensor.extra_state_attributes["reason"] == reason
