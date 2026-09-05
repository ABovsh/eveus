"""The availability grace window must HOLD the last reading, not blank it.

A single missed poll used to leave every charger-backed entity *available and
empty*: `_get_data_value` returns None while the updater is offline, so Home
Assistant wrote `unknown` for ~36 entities, and only 30-60 seconds later did the
entity turn `unavailable`.

`unknown` is strictly worse than `unavailable` for a consumer. It is a state,
so helpers ingest it: on 2026-09-05 a charger timeout produced

    EV Charger Daily Energy received an invalid new state
    from sensor.eveus_ev_charger_total_energy : unknown

from two `utility_meter` helpers, plus a recorder row per entity on the way
down and another on the way back up. The grace window exists to ride out one
failed poll; blanking through it defeated its whole purpose.

The rule these tests pin: while the entity is inside the grace window it keeps
publishing what it last read, and it goes `unavailable` — never `unknown` —
when the window closes. An entity that can still compute something real while
the charger is unreachable (Connection Quality) must keep updating.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from homeassistant.helpers.entity import EntityCategory

from conftest import EveusTestUpdater, disable_state_writes
from custom_components.eveus import common_base
from custom_components.eveus.binary_sensor import EveusCarConnectedBinarySensor
from custom_components.eveus.const import AVAILABILITY_GRACE_PERIOD
from custom_components.eveus.sensor_definitions import (
    OptimizedEveusSensor,
    SensorSpec,
    SensorType,
)

_START = 1_000_000.0


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch):
    """Drive the grace window by hand, and swallow its scheduled recheck."""
    now = {"t": _START}
    monkeypatch.setattr(common_base.time, "monotonic", lambda: now["t"])
    monkeypatch.setattr(
        common_base, "async_call_later", lambda *_a, **_k: (lambda: None)
    )
    return now


def _spec(value_fn, *, offline: bool = False, attributes_fn=None) -> SensorSpec:
    return SensorSpec(
        key="test_sensor",
        name="Test Sensor",
        value_fn=value_fn,
        sensor_type=SensorType.MEASUREMENT,
        icon="mdi:test-tube",
        device_class="energy",
        state_class="total_increasing",
        unit="kWh",
        precision=2,
        category=EntityCategory.DIAGNOSTIC,
        attributes_fn=attributes_fn,
        available_when_offline=offline,
    )


def _sensor(updater, spec: SensorSpec) -> OptimizedEveusSensor:
    entity = OptimizedEveusSensor(updater, spec)
    entity.hass = SimpleNamespace(config=SimpleNamespace(time_zone="Europe/Kiev"))
    disable_state_writes(entity)
    return entity


def _payload_value(updater, _hass):
    """Stand-in for every charger-backed getter: blank while offline."""
    if not updater.available:
        return None
    return float(updater.data["totalEnergy"])


def _payload_attrs(updater, _hass):
    if not updater.available:
        return {}
    return {"raw": updater.data["totalEnergy"]}


def _go_offline(entity, updater) -> None:
    updater.available = False
    entity._handle_coordinator_update()


def _age_out_the_grace_window(entity, clock) -> None:
    """Push the monotonic clock past the grace period and re-poll."""
    clock["t"] = _START + AVAILABILITY_GRACE_PERIOD + 1
    entity._handle_coordinator_update()


# --- Sensors ---


def test_sensor_holds_its_reading_through_the_grace_window(clock) -> None:
    updater = EveusTestUpdater({"totalEnergy": "5178.63"})
    entity = _sensor(updater, _spec(_payload_value))
    entity._handle_coordinator_update()
    assert entity.native_value == 5178.63

    _go_offline(entity, updater)

    assert entity.available is True, "still inside the grace window"
    assert entity.native_value == 5178.63, "a missed poll must not blank the value"


def test_sensor_never_publishes_unknown_before_it_publishes_unavailable(clock) -> None:
    """The utility_meter regression, stated as an invariant.

    Every state this entity publishes while the charger is unreachable must be
    either the last real reading or nothing at all — never a blank that is
    still 'available', because that is what reaches a helper as `unknown`.
    """
    updater = EveusTestUpdater({"totalEnergy": "5178.63"})
    entity = _sensor(updater, _spec(_payload_value))
    entity._handle_coordinator_update()

    _go_offline(entity, updater)
    for _ in range(3):
        entity._handle_coordinator_update()
        assert not (entity.available and entity.native_value is None)

    _age_out_the_grace_window(entity, clock)
    assert entity.available is False


def test_sensor_attributes_survive_the_grace_window(clock) -> None:
    """Attributes write a recorder row exactly like a state does."""
    updater = EveusTestUpdater({"totalEnergy": "5178.63"})
    entity = _sensor(updater, _spec(_payload_value, attributes_fn=_payload_attrs))
    entity._handle_coordinator_update()
    assert entity.extra_state_attributes == {"raw": "5178.63"}

    _go_offline(entity, updater)

    assert entity.extra_state_attributes == {"raw": "5178.63"}


def test_sensor_resumes_the_live_reading_when_the_charger_answers_again(clock) -> None:
    """A hold is a hold, not a freeze."""
    updater = EveusTestUpdater({"totalEnergy": "5178.63"})
    entity = _sensor(updater, _spec(_payload_value))
    entity._handle_coordinator_update()
    _go_offline(entity, updater)

    updater.available = True
    updater.data["totalEnergy"] = "5180.00"
    entity._handle_coordinator_update()

    assert entity.native_value == 5180.0


def test_an_offline_capable_sensor_keeps_updating_during_the_grace_window(clock) -> None:
    """Connection Quality is the reading a user most wants DURING an outage.

    Holding the last value must be scoped to what the charger's payload feeds;
    a sensor that computes something real while polls fail has to keep moving,
    or the hold has replaced one wrong answer with another.
    """
    readings = iter([100, 95, 90])
    updater = EveusTestUpdater({}, available=True)
    entity = _sensor(updater, _spec(lambda *_a: next(readings), offline=True))
    entity._handle_coordinator_update()
    assert entity.native_value == 100

    _go_offline(entity, updater)
    assert entity.native_value == 95

    entity._handle_coordinator_update()
    assert entity.native_value == 90


# --- Binary sensors, same rule ---


def test_binary_sensor_holds_its_reading_through_the_grace_window(clock) -> None:
    updater = EveusTestUpdater({"state": 4})
    entity = EveusCarConnectedBinarySensor(updater)
    disable_state_writes(entity)
    entity._handle_coordinator_update()
    assert entity.is_on is True

    _go_offline(entity, updater)

    assert entity.available is True
    assert entity.is_on is True, "a missed poll must not blank the plug state"


def test_binary_sensor_goes_unavailable_rather_than_blank(clock) -> None:
    updater = EveusTestUpdater({"state": 4})
    entity = EveusCarConnectedBinarySensor(updater)
    disable_state_writes(entity)
    entity._handle_coordinator_update()
    _go_offline(entity, updater)

    _age_out_the_grace_window(entity, clock)

    assert entity.available is False
    assert entity.is_on is None
