"""Last Session sensors capture the summary of the most recent charging session."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from custom_components.eveus.session_history import (
    LastSessionCostSensor,
    LastSessionDurationSensor,
    LastSessionEnergySensor,
    LastSessionFinalSocSensor,
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


def test_factory_creates_three_sensors_without_soc_calculator() -> None:
    sensors = create_last_session_sensors(_updater(), 1, None)
    assert len(sensors) == 3
    assert not any(isinstance(s, LastSessionFinalSocSensor) for s in sensors)


def test_factory_adds_final_soc_with_calculator() -> None:
    sensors = create_last_session_sensors(_updater(), 1, Mock())
    assert len(sensors) == 4


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


def test_final_soc_uses_calculator_with_session_energy() -> None:
    calc = Mock()
    calc.get_soc_percent.return_value = 79.6
    sensor = LastSessionFinalSocSensor(_updater(), 1, calc)
    sensor._handle_finished_event(_event())
    calc.get_soc_percent.assert_called_once_with(18.46)
    assert sensor.native_value == pytest.approx(79.6)


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
