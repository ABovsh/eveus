"""Behavior tests for the 4.5.0 sensors: Session Cost, Charging Finish Time,
Car Connected binary sensor, and the shared `calculate_remaining_seconds` math.

These tests assert observable behavior of the value/getter functions and
entities, not just plumbing — they include truth tables for edge cases so
future regressions in offline/missing-helper/target-reached paths fail loudly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from custom_components.eveus import sensor_definitions as sensors
from custom_components.eveus import utils
from custom_components.eveus.binary_sensor import (
    EveusCarConnectedBinarySensor,
    _CONNECTED_STATES,
)
from custom_components.eveus.ev_sensors import (
    CachedSOCCalculator,
    ChargingFinishTimeSensor,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class _States:
    def __init__(self, values: dict[str, object]) -> None:
        self._values = values

    def get(self, entity_id: str) -> SimpleNamespace | None:
        v = self._values.get(entity_id)
        return None if v is None else SimpleNamespace(state=str(v))


class _Hass:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.states = _States(values or {})


class _Updater:
    host = "192.168.1.50"
    available = True
    last_update_success = True
    scheme = "http"

    def __init__(self, data: dict[str, object], *, available: bool = True) -> None:
        self.data = data
        self.available = available
        self.connection_quality = {}

    def async_add_listener(self, *args, **kwargs):
        return lambda: None


HELPERS = {
    "input_number.ev_initial_soc": 20,
    "input_number.ev_battery_capacity": 80,
    "input_number.ev_soc_correction": 10,
    "input_number.ev_target_soc": 80,
}


# ---------------------------------------------------------------------------
# calculate_remaining_seconds — truth table
# ---------------------------------------------------------------------------

class TestCalculateRemainingSeconds:
    def test_returns_positive_seconds_when_charging(self) -> None:
        # 20% → 80% on an 80 kWh battery with 10% correction, 7 kW.
        # (80-20)/100 * 80 = 48 kWh needed; at 7 kW * 0.9 = 6.3 kW.
        # 48 / 6.3 * 3600 ≈ 27428 s.
        seconds = utils.calculate_remaining_seconds(20, 80, 7000, 80, 10)
        assert seconds is not None
        assert abs(seconds - 27428.57) < 1.0

    def test_returns_zero_when_target_already_reached(self) -> None:
        # Target reached must be a distinct, actionable result.
        assert utils.calculate_remaining_seconds(80, 80, 7000, 80, 10) == 0.0
        assert utils.calculate_remaining_seconds(90, 80, 7000, 80, 10) == 0.0

    def test_returns_none_when_not_charging(self) -> None:
        # Zero/negative power → no meaningful ETA. Critical: must NOT return 0.0,
        # otherwise the Finish Time sensor would render "now" indefinitely.
        assert utils.calculate_remaining_seconds(20, 80, 0, 80, 10) is None
        assert utils.calculate_remaining_seconds(20, 80, -100, 80, 10) is None

    def test_returns_none_on_invalid_inputs(self) -> None:
        # Out-of-range SoC
        assert utils.calculate_remaining_seconds(120, 80, 7000, 80, 10) is None
        assert utils.calculate_remaining_seconds(20, 150, 7000, 80, 10) is None
        # Missing values
        assert utils.calculate_remaining_seconds(None, 80, 7000, 80, 10) is None
        assert utils.calculate_remaining_seconds(20, 80, 7000, None, 10) is None
        # Zero/negative capacity
        assert utils.calculate_remaining_seconds(20, 80, 7000, 0, 10) is None
        assert utils.calculate_remaining_seconds(20, 80, 7000, -5, 10) is None

    def test_correction_of_100_percent_yields_no_eta(self) -> None:
        # 100% correction → all power lost → power_kw <= 0.
        assert utils.calculate_remaining_seconds(20, 80, 7000, 80, 100) is None

    def test_default_correction_is_used_when_none(self) -> None:
        # Same math as test_returns_positive_seconds_when_charging but with
        # correction=None; helper must default to 7.5%, not crash.
        seconds = utils.calculate_remaining_seconds(20, 80, 7000, 80, None)
        assert seconds is not None and seconds > 0

    def test_remaining_time_and_seconds_agree_on_target_reached(self) -> None:
        # The two public helpers must agree on which inputs mean "done".
        assert utils.calculate_remaining_seconds(80, 80, 7000, 80, 10) == 0.0
        assert utils.calculate_remaining_time(80, 80, 7000, 80, 10) == "Target reached"

    def test_remaining_time_and_seconds_agree_on_not_charging(self) -> None:
        assert utils.calculate_remaining_seconds(20, 80, 0, 80, 10) is None
        assert utils.calculate_remaining_time(20, 80, 0, 80, 10) == "Not charging"


# ---------------------------------------------------------------------------
# Session Cost
# ---------------------------------------------------------------------------

class TestSessionCost:
    def test_multiplies_session_energy_by_active_rate(self) -> None:
        updater = _Updater({
            "sessionEnergy": "12.34",
            "activeTarif": "0",
            "tarif": "264",  # 2.64 ₴/kWh
        })
        # 12.34 kWh * 2.64 ₴/kWh = 32.5776 → rounded to 32.58 ₴
        assert sensors.get_session_cost(updater, None) == 32.58

    def test_returns_none_when_offline(self) -> None:
        updater = _Updater(
            {"sessionEnergy": "10", "activeTarif": "0", "tarif": "264"},
            available=False,
        )
        # No fake value, no zero — must be None so HA hides the entity.
        assert sensors.get_session_cost(updater, None) is None

    def test_returns_none_when_active_rate_unknown(self) -> None:
        # `activeTarif` missing → cannot compute cost → must NOT silently
        # return 0 (that would falsely imply "no charging cost").
        updater = _Updater({"sessionEnergy": "10"})
        assert sensors.get_session_cost(updater, None) is None

    def test_returns_none_when_session_energy_missing(self) -> None:
        updater = _Updater({"activeTarif": "0", "tarif": "264"})
        assert sensors.get_session_cost(updater, None) is None

    def test_zero_energy_yields_zero_cost(self) -> None:
        # Distinct from None: device online, rate known, but session hasn't
        # produced any kWh yet → 0.00 ₴ is a real, meaningful value.
        updater = _Updater({
            "sessionEnergy": "0",
            "activeTarif": "0",
            "tarif": "264",
        })
        assert sensors.get_session_cost(updater, None) == 0.0

    def test_rate_2_is_used_when_active(self) -> None:
        updater = _Updater({
            "sessionEnergy": "10",
            "activeTarif": "1",
            "tarif": "264",
            "tarifAValue": "132",  # 1.32 ₴/kWh
            "tarifBValue": "400",
        })
        assert sensors.get_session_cost(updater, None) == 13.2

    def test_sensor_spec_is_registered_with_uah_unit(self) -> None:
        specs = {s.name: s for s in sensors.get_sensor_specifications()}
        assert "Session Cost" in specs
        spec = specs["Session Cost"]
        assert spec.unit == "₴"
        assert spec.precision == 2
        assert spec.state_class == SensorStateClass.MEASUREMENT


# ---------------------------------------------------------------------------
# Charging Finish Time
# ---------------------------------------------------------------------------

_FIXED_NOW = datetime(2026, 5, 16, 10, 0, 0, tzinfo=timezone.utc)


def _finish_sensor(updater_data: dict, helpers: dict | None = HELPERS):
    calc = CachedSOCCalculator()
    sensor = ChargingFinishTimeSensor(_Updater(updater_data), 1, calc)
    sensor.hass = _Hass(helpers or {})
    return sensor


class TestChargingFinishTime:
    def test_returns_future_timestamp_during_active_charging(self) -> None:
        sensor = _finish_sensor({"IEM1": "0", "powerMeas": "7000"})
        with patch(
            "custom_components.eveus.ev_sensors.dt_util.utcnow",
            return_value=_FIXED_NOW,
        ):
            result = sensor._get_sensor_value()
        # ~27428s ahead, rounded up to the next whole minute.
        assert result is not None
        delta = result - _FIXED_NOW
        assert timedelta(hours=7, minutes=30) < delta < timedelta(hours=8)
        # Must be a tz-aware UTC timestamp suitable for device_class=timestamp.
        assert result.tzinfo is not None
        # Must be minute-aligned to avoid jitter on every poll.
        assert result.second == 0 and result.microsecond == 0

    def test_returns_none_when_not_charging(self) -> None:
        # powerMeas=0 → no ETA. Must be None (timestamp sensors hide on None).
        sensor = _finish_sensor({"IEM1": "0", "powerMeas": "0"})
        assert sensor._get_sensor_value() is None

    def test_returns_none_when_helpers_missing(self) -> None:
        sensor = _finish_sensor({"IEM1": "0", "powerMeas": "7000"}, helpers={})
        assert sensor._get_sensor_value() is None

    def test_returns_none_when_target_reached(self) -> None:
        # initial_soc + energy ≥ target → seconds == 0 → must return None,
        # not "now". Otherwise the timestamp would point to the past forever.
        # 80 kWh battery, initial 20%, target 80% → need 48 kWh delivered.
        helpers_full = dict(HELPERS)
        sensor = _finish_sensor({"IEM1": "100", "powerMeas": "7000"}, helpers_full)
        # Energy_charged is the delta from baseline; first read anchors at 100,
        # then we bump to 200 to deliver 100 kWh (more than 48 needed).
        sensor._get_sensor_value()
        sensor._updater.data = {"IEM1": "200", "powerMeas": "7000"}
        assert sensor._get_sensor_value() is None

    def test_jitters_only_on_minute_boundary(self) -> None:
        sensor = _finish_sensor({"IEM1": "0", "powerMeas": "7000"})
        # Two close polls a few seconds apart must yield the same minute-stamp.
        with patch(
            "custom_components.eveus.ev_sensors.dt_util.utcnow",
            return_value=_FIXED_NOW,
        ):
            first = sensor._get_sensor_value()
        with patch(
            "custom_components.eveus.ev_sensors.dt_util.utcnow",
            return_value=_FIXED_NOW + timedelta(seconds=3),
        ):
            second = sensor._get_sensor_value()
        # Within the same minute, both round to the same boundary.
        assert first == second

    def test_uses_timestamp_device_class(self) -> None:
        # Critical for HA UI / automations: device_class must be TIMESTAMP,
        # otherwise the value would be rendered as a plain string.
        # HA's CachedProperties metaclass stores attrs under __attr_* keys.
        assert (
            vars(ChargingFinishTimeSensor).get("__attr_device_class")
            == SensorDeviceClass.TIMESTAMP
        )


# ---------------------------------------------------------------------------
# Car Connected binary sensor
# ---------------------------------------------------------------------------

class TestCarConnectedBinarySensor:
    def test_connected_states_match_canonical_mapping(self) -> None:
        # Locking this set down: 3=Connected, 4=Charging, 5=Complete, 6=Paused.
        # Any change here is a behavior change and should require an explicit
        # update to this assertion.
        assert _CONNECTED_STATES == frozenset({3, 4, 5, 6})

    def _make(self, data: dict, *, available: bool = True):
        sensor = EveusCarConnectedBinarySensor(_Updater(data, available=available), 1)
        sensor._entity_available = available
        return sensor

    def test_is_on_for_each_connected_state(self) -> None:
        for state in (3, 4, 5, 6):
            sensor = self._make({"state": state})
            assert sensor.is_on is True, f"state={state} should be connected"

    def test_is_off_for_disconnected_states(self) -> None:
        # 0=Startup, 1=System Test, 2=Standby, 7=Error → no plug presence.
        for state in (0, 1, 2, 7):
            sensor = self._make({"state": state})
            assert sensor.is_on is False, f"state={state} should be disconnected"

    def test_returns_none_when_unavailable(self) -> None:
        # When the charger is offline, plug presence is unknown — not False.
        sensor = self._make({"state": 4}, available=False)
        assert sensor.is_on is None

    def test_returns_none_when_state_missing(self) -> None:
        # Defensive: empty payload should not infer a plug state.
        sensor = self._make({})
        assert sensor.is_on is None

    def test_returns_none_when_state_unparseable(self) -> None:
        # Garbage payload must not crash and must not lie.
        sensor = self._make({"state": "garbage"})
        assert sensor.is_on is None

    def test_uses_plug_device_class(self) -> None:
        # HA's CachedProperties metaclass stores attrs under __attr_* keys.
        assert (
            vars(EveusCarConnectedBinarySensor).get("__attr_device_class")
            == BinarySensorDeviceClass.PLUG
        )

    def test_unique_id_follows_eveus_convention(self) -> None:
        sensor = self._make({"state": 4})
        # eveus_car_connected for device 1; "eveus2_..." for device 2.
        assert sensor.unique_id == "eveus_car_connected"
        sensor2 = EveusCarConnectedBinarySensor(_Updater({"state": 4}), 2)
        assert sensor2.unique_id == "eveus2_car_connected"
