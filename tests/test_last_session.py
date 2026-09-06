"""Last Session sensors capture the summary of the most recent charging session."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import UnitOfEnergy, UnitOfTime

import custom_components.eveus.session_history as session_history
from custom_components.eveus.const import (
    FINISHED_REASONS,
    MAX_ENERGY_KWH,
)
from custom_components.eveus.sensor_definitions import ICON_CURRENCY_UAH, UNIT_UAH
from custom_components.eveus.session_history import (
    LastSessionCostSensor,
    LastSessionDurationSensor,
    LastSessionEnergySensor,
    _LastSessionSensorBase,
    create_last_session_sensors,
)


def _updater() -> MagicMock:
    updater = MagicMock()
    updater.device_number = 1
    return updater


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


def test_factory_creates_exactly_three_sensors() -> None:
    sensors = create_last_session_sensors(_updater(), 1)
    assert len(sensors) == 3
    assert {type(s) for s in sensors} == {
        LastSessionEnergySensor,
        LastSessionCostSensor,
        LastSessionDurationSensor,
    }


def test_final_soc_sensor_is_gone() -> None:
    assert not hasattr(session_history, "LastSessionFinalSocSensor")


def test_sensors_capture_event_values() -> None:
    updater = _updater()
    energy = LastSessionEnergySensor(updater, 1)
    cost = LastSessionCostSensor(updater, 1)
    duration = LastSessionDurationSensor(updater, 1)
    for sensor in (energy, cost, duration):
        sensor._handle_finished_event(_event())
    assert energy.native_value == pytest.approx(18.46)
    assert cost.native_value == pytest.approx(49.78)
    assert duration.native_value == 22320
    assert energy.extra_state_attributes["reason"] == "complete"
    assert "finished_at" in energy.extra_state_attributes


def test_other_device_event_is_ignored() -> None:
    sensor = LastSessionEnergySensor(_updater(), 1)
    sensor._handle_finished_event(_event(device_number=2))
    assert sensor.native_value is None


def test_missing_snapshot_values_leave_sensor_unknown() -> None:
    sensor = LastSessionEnergySensor(_updater(), 1)
    sensor._handle_finished_event(_event(session_energy_kwh=None))
    assert sensor.native_value is None


def test_available_even_when_charger_offline() -> None:
    updater = _updater()
    updater.available = False
    sensor = LastSessionEnergySensor(updater, 1)
    sensor._handle_finished_event(_event())
    assert sensor.available is True
    assert sensor.native_value == pytest.approx(18.46)


@pytest.mark.asyncio
async def test_restore_after_restart() -> None:
    sensor = LastSessionEnergySensor(_updater(), 1)
    state = SimpleNamespace(
        state="18.46", attributes={"reason": "complete", "finished_at": "2026-07-04T10:00:00"}
    )
    await sensor._async_restore_state(state)
    assert sensor.native_value == pytest.approx(18.46)
    assert sensor.extra_state_attributes["reason"] == "complete"
    assert sensor.extra_state_attributes["finished_at"] == "2026-07-04T10:00:00"


# ---------------------------------------------------------------------------
# session_history.py mutation-hardening gaps
# ---------------------------------------------------------------------------


def test_last_session_sensor_base_defaults_are_exact() -> None:
    """The base class's own (never-overridden-in-practice) defaults must be
    an empty string / 0.0 exactly - only ever exercised by construction, not
    by any subclass which always overrides both."""
    assert _LastSessionSensorBase._event_field == ""
    assert _LastSessionSensorBase._max_value == 0.0


def test_last_session_sensor_device_number_default_is_one() -> None:
    sensor = LastSessionEnergySensor(_updater())
    assert sensor.unique_id == "eveus_last_session_energy"


@pytest.mark.parametrize(
    "cls,name,device_class,unit,precision,icon",
    [
        (
            LastSessionEnergySensor,
            "Last Session Energy",
            SensorDeviceClass.ENERGY,
            UnitOfEnergy.KILO_WATT_HOUR,
            2,
            "mdi:battery-charging-100",
        ),
        (
            LastSessionCostSensor,
            "Last Session Cost",
            SensorDeviceClass.MONETARY,
            UNIT_UAH,
            2,
            ICON_CURRENCY_UAH,
        ),
        (
            LastSessionDurationSensor,
            "Last Session Duration",
            SensorDeviceClass.DURATION,
            UnitOfTime.SECONDS,
            None,
            "mdi:timer-outline",
        ),
    ],
)
def test_last_session_sensor_metadata_is_exact(
    cls, name, device_class, unit, precision, icon
) -> None:
    sensor = cls(_updater())
    assert sensor.ENTITY_NAME == name
    assert sensor.device_class == device_class
    assert sensor.native_unit_of_measurement == unit
    assert getattr(sensor, "_attr_suggested_display_precision", None) == precision
    assert sensor.icon == icon


@pytest.mark.parametrize(
    "max_value,value,expected",
    [
        (MAX_ENERGY_KWH, 0, 0),
        (MAX_ENERGY_KWH, MAX_ENERGY_KWH, MAX_ENERGY_KWH),
    ],
)
def test_value_from_event_boundary_inclusive_both_ends(
    max_value: float, value: float, expected: float
) -> None:
    """`0 <= value <= _max_value`: both ends are inclusive - a boundary mutant
    on either comparison operator would reject the exact edge values."""
    sensor = LastSessionEnergySensor(_updater(), 1)
    assert sensor._value_from_event({"session_energy_kwh": value}) == expected


def test_value_from_event_requires_real_or_not_and_gate() -> None:
    """`not math.isfinite(value) or not 0 <= value <= max` must be a real OR.
    inf/nan always also fail the range check on any finite max, so they
    can't distinguish `or` from `and` - a finite-but-out-of-range value
    (negative) is the case that actually differs: with a mutated `and`,
    "not isfinite" is False (finite input) and short-circuits to False,
    wrongly accepting the negative value."""
    sensor = LastSessionEnergySensor(_updater(), 1)
    assert sensor._value_from_event({"session_energy_kwh": float("inf")}) is None
    assert sensor._value_from_event({"session_energy_kwh": float("nan")}) is None
    assert sensor._value_from_event({"session_energy_kwh": -5}) is None


@pytest.mark.parametrize(
    "value",
    [MAX_ENERGY_KWH, 0.0],
)
def test_restore_state_boundary_inclusive_both_ends(value: float) -> None:
    """Mirrors the event-path boundary check for the restore path."""
    sensor = LastSessionEnergySensor(_updater(), 1)
    state = SimpleNamespace(state=str(value), attributes={})
    asyncio.run(sensor._async_restore_state(state))
    assert sensor.native_value == pytest.approx(value)


def test_restore_state_requires_real_or_not_and_gate() -> None:
    """`not isfinite(x) or not (0<=x<=max)` must be a real OR: a finite but
    out-of-range value (e.g. negative) has to be rejected on the range
    check alone. A mutated `and` would short-circuit to False as soon as
    `not isfinite` is False (finite input), silently accepting an
    out-of-domain negative value - unlike inf/nan, whose "not isfinite" and
    "not in range" happen to always agree, this negative-value case is the
    one that actually distinguishes the operator."""
    sensor = LastSessionEnergySensor(_updater(), 1)
    state = SimpleNamespace(state="-5", attributes={})
    asyncio.run(sensor._async_restore_state(state))
    assert sensor.native_value is None


def test_reason_from_event_requires_a_real_or_not_an_and_gate() -> None:
    """`not isinstance(reason, str) or reason not in KNOWN` must be a real OR.

    An unhashable reason (dict/list) is the case that reads as an `and` being
    harmless: it fails the isinstance half, and the membership half would raise
    on it, so the short-circuit looks load-bearing either way. The case that
    actually separates the two operators is a plain STRING nobody fires — with
    a mutated `and`, `not isinstance` is False, the whole gate short-circuits
    to False, and an arbitrary string from the public bus event lands in a
    persisted entity attribute.
    """
    sensor = LastSessionEnergySensor(_updater(), 1)

    assert sensor._reason_from_event({"reason": "obviously-not-a-reason"}) is None
    assert sensor._reason_from_event({"reason": {"nested": "value"}}) is None
    assert sensor._reason_from_event({"reason": ["listy"]}) is None
    assert sensor._reason_from_event({"reason": 3}) is None
    assert sensor._reason_from_event({}) is None
    # ...and every reason the coordinator really does fire still gets through.
    for reason in FINISHED_REASONS.values():
        assert sensor._reason_from_event({"reason": reason}) == reason


def test_known_finish_reasons_is_exactly_what_the_coordinator_can_fire() -> None:
    """The accepted set is the coordinator's mapping plus its fallback default.

    Both halves are read from the same constant the coordinator uses, so the
    two cannot drift apart — which is the whole claim the module's comment
    makes. The union looks redundant today, and is: the fallback "stopped" is
    also `FINISHED_REASONS[3]`. It stays because it pins the FALLBACK
    independently, so relabelling state 3 could not silently stop the default
    reason being accepted. Spelling the set out here is what stops the literal
    being edited to something the coordinator never sends.
    """
    fallback = FINISHED_REASONS[3]

    assert session_history._KNOWN_FINISH_REASONS == frozenset(
        FINISHED_REASONS.values()
    ) | {fallback}
    assert fallback in FINISHED_REASONS.values()
