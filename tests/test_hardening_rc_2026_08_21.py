"""Hardening tests for the 2026-08-21 audit round."""
from __future__ import annotations

from conftest import EV_HELPERS, EveusTestUpdater
from custom_components.eveus.ev_sensors import EVSocKwhSensor, EVSocPercentSensor

from test_ev_sensor_entities import push_helpers


def _sensor(cls, data: dict):
    sensor = cls(EveusTestUpdater(data))
    push_helpers(sensor._soc_calculator, EV_HELPERS)
    return sensor


def test_soc_kwh_goes_unknown_when_session_energy_absent_mid_session() -> None:
    """An active session with sessionEnergy missing must not read as 0 kWh delivered."""
    sensor = _sensor(EVSocKwhSensor, {"state": 4})
    assert sensor._get_sensor_value() is None


def test_soc_percent_goes_unknown_when_session_energy_absent_mid_session() -> None:
    """Same for SOC Percent: 0 delivered would snap the graph down to Initial SOC."""
    sensor = _sensor(EVSocPercentSensor, {"state": 4})
    assert sensor._get_sensor_value() is None


def test_soc_sensors_still_use_zero_fallback_outside_a_session() -> None:
    """Before a session starts, an absent sessionEnergy still means 0 delivered."""
    kwh = _sensor(EVSocKwhSensor, {"state": 2})
    pct = _sensor(EVSocPercentSensor, {"state": 2})
    assert kwh._get_sensor_value() is not None
    assert pct._get_sensor_value() == 20
