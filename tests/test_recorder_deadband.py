"""Recorder-churn damping: values hold until they move by a meaningful step.

Home Assistant writes a database row on every state OR attribute change, so a
reading that dithers by one unit between two consecutive polls costs one row
per poll forever. Rounding does not fix that — a value straddling a rounding
boundary still flips every poll — so each of these sensors holds its LAST
PUBLISHED value until the new reading moves by at least its deadband.

Deliberately NOT damped: every accumulating meter (Total/Counter/Session
Energy, Session/Counter Cost). Holding those back would drop the withheld tail
permanently when the charger clears the counter, and they feed long-term
statistics.
"""
from __future__ import annotations

import pytest
from conftest import EV_HELPERS, EveusTestUpdater
from datetime import datetime
from types import SimpleNamespace

from homeassistant.util import dt as dt_util

from custom_components.eveus import ev_sensors
from custom_components.eveus import utils
from custom_components.eveus import sensor_definitions as sd
from custom_components.eveus.ev_sensors import (
    CachedSOCCalculator,
    ChargingFinishTimeSensor,
    CostToTargetSocSensor,
    EnergyToTargetSocSensor,
    EVSocKwhSensor,
    TimeToTargetSocSensor,
)


def _updater(data: dict[str, object], **extra) -> SimpleNamespace:
    fields: dict[str, object] = {
        "data": data,
        "available": True,
        "connection_quality": {},
        "host": "192.168.1.50",
    }
    fields.update(extra)
    return SimpleNamespace(**fields)


def _push(calculator: CachedSOCCalculator) -> CachedSOCCalculator:
    for key, entity in (
        ("initial_soc", "input_number.ev_initial_soc"),
        ("battery_capacity", "input_number.ev_battery_capacity"),
        ("soc_correction", "input_number.ev_soc_correction"),
        ("target_soc", "input_number.ev_target_soc"),
    ):
        calculator.set_value(key, EV_HELPERS[entity])
    return calculator


def _read(getter, updater, key: str, values) -> list:
    """Feed successive payload values through one getter, sharing the updater."""
    out = []
    for value in values:
        updater.data[key] = value
        out.append(getter(updater, None))
    return out


# --- payload getters: voltage / current / power / RSSI ---


@pytest.mark.parametrize(
    ("getter", "key", "feed", "expected"),
    [
        # Voltage dithers ±1 V on a healthy grid; 2 V is the smallest step worth a row.
        (sd.get_voltage, "voltMeas1", [230, 231, 229, 232], [230, 230, 230, 232]),
        # Current sits on 15.9/16.0/16.1 for a whole session.
        (sd.get_current, "curMeas1", [16.0, 16.1, 15.9, 16.3], [16.0, 16.0, 16.0, 16.3]),
        # Power wanders by tens of watts under a constant 3.5 kW draw.
        (sd.get_power, "powerMeas", [3500, 3520, 3480, 3560], [3500, 3500, 3500, 3560]),
        # RSSI wanders across several dBm between polls while the link is
        # unchanged; only a swing wide enough to change the verdict is a row.
        (sd.get_wifi_rssi, "RSSI", [-66, -67, -65, -72], [-66, -66, -66, -72]),
    ],
)
def test_dithering_getters_hold_until_the_deadband_is_crossed(
    getter, key, feed, expected
) -> None:
    updater = _updater({})

    assert _read(getter, updater, key, feed) == pytest.approx(expected)


def test_deadband_always_publishes_an_exact_zero() -> None:
    """Charging stopping must show 0 W immediately, not a held 40 W.

    The second case is the one that needs the explicit zero rule: a standby
    reading is itself already inside the 50 W band, so a plain distance check
    would hold "30 W" on the poll the contactor opens and never publish the 0.
    """
    updater = _updater({})
    assert _read(sd.get_power, updater, "powerMeas", [3500, 3480, 0]) == [3500, 3500, 0]

    standby = _updater({})
    assert _read(sd.get_power, standby, "powerMeas", [30, 0, 20]) == [30, 0, 0]


def test_deadband_does_not_leak_between_chargers() -> None:
    """Two config entries poll two different chargers; state is per updater."""
    first, second = _updater({"voltMeas1": 230}), _updater({"voltMeas1": 245})

    assert sd.get_voltage(first, None) == 230
    assert sd.get_voltage(second, None) == 245


def test_offline_reading_keeps_the_last_value_as_the_reference() -> None:
    """A None (offline) reading is passed through without resetting the anchor."""
    updater = _updater({"voltMeas1": 230})
    assert sd.get_voltage(updater, None) == 230

    updater.available = False
    assert sd.get_voltage(updater, None) is None

    updater.available = True
    updater.data["voltMeas1"] = 231
    assert sd.get_voltage(updater, None) == 230


def test_connection_quality_attribute_reuses_the_damped_rssi() -> None:
    """The wifi_rssi attribute was the single largest source of recorder rows.

    It mirrors the WiFi Signal sensor, so it must mirror its damping too —
    otherwise Connection Quality writes a row per poll while its own state
    (poll success rate) sits at 100 % for days.
    """
    updater = _updater({"RSSI": -66}, connection_quality={"success_rate": 100})
    assert sd.get_connection_attrs(updater, None)["wifi_rssi"] == -66

    updater.data["RSSI"] = -67

    assert sd.get_connection_attrs(updater, None)["wifi_rssi"] == -66


# --- SOC / forecast entities ---


def _soc_sensor(cls, session_energy: str, **payload):
    data = {"sessionEnergy": session_energy, "state": 4, "powerMeas": "7000"}
    data.update(payload)
    return cls(EveusTestUpdater(data), 1, _push(CachedSOCCalculator()))


@pytest.mark.parametrize(
    ("cls", "first", "held", "crossed"),
    [
        # SOC Energy: 0.01 kWh resolution moved every 10 s at 3.5 kW.
        (EVSocKwhSensor, "10", "10.05", "10.5"),
        # Energy to Target: a forecast, shown to one decimal.
        (EnergyToTargetSocSensor, "10", "10.05", "11.0"),
    ],
)
def test_soc_forecast_sensors_hold_within_their_deadband(
    cls, first, held, crossed
) -> None:
    sensor = _soc_sensor(cls, first)
    sensor._update_native_value()
    baseline = sensor._attr_native_value
    assert baseline is not None

    sensor._updater.data["sessionEnergy"] = held
    sensor._update_native_value()
    assert sensor._attr_native_value == baseline

    sensor._updater.data["sessionEnergy"] = crossed
    sensor._update_native_value()
    assert sensor._attr_native_value != baseline


def test_cost_to_target_holds_within_one_currency_unit() -> None:
    """Displayed with no decimals at all — sub-hryvnia rows are invisible.

    The middle step is what pins the 1 UAH band rather than the 0.25 kWh one
    this sensor inherits from Energy to Target: it moves the cost by ~0.8 UAH,
    far enough that the parent's band would have published it.
    """
    sensor = _soc_sensor(CostToTargetSocSensor, "10", tarif="400", activeTarif="0")
    sensor._update_native_value()
    baseline = sensor._attr_native_value
    assert baseline is not None

    sensor._updater.data["sessionEnergy"] = "10.05"
    sensor._update_native_value()
    assert sensor._attr_native_value == baseline

    sensor._updater.data["sessionEnergy"] = "10.2"
    sensor._update_native_value()
    assert sensor._attr_native_value == baseline

    sensor._updater.data["sessionEnergy"] = "10.5"
    sensor._update_native_value()
    assert sensor._attr_native_value != baseline


def test_time_to_target_snaps_to_a_five_minute_grid() -> None:
    sensor = _soc_sensor(TimeToTargetSocSensor, "0", powerMeas="7000")

    value = sensor._get_sensor_value()

    minutes = int(value.split("h ")[1].rstrip("m"))
    assert minutes % 5 == 0


@pytest.mark.parametrize("session_energy", ["0", "1", "2.5", "7", "13.75"])
def test_charging_finish_time_snaps_to_a_five_minute_grid(session_energy: str) -> None:
    """Every estimate lands on a 5-minute boundary, and never in the past."""
    sensor = _soc_sensor(ChargingFinishTimeSensor, session_energy, powerMeas="7000")

    finish = sensor._get_sensor_value()

    assert finish.minute % 5 == 0
    assert (finish.second, finish.microsecond) == (0, 0)
    assert finish > dt_util.utcnow()


# --- ETA estimates: a grid alone does not stop boundary flipping ---


def _freeze(monkeypatch, moment: datetime) -> None:
    monkeypatch.setattr(ev_sensors.dt_util, "utcnow", lambda: moment)


def _feed_seconds(monkeypatch, first: float) -> dict:
    """Drive the one calculation both estimates resolve through.

    The returned dict is the poll: set ``["seconds"]`` to move the estimate,
    so the series is what the charger reports, not how many times a sensor
    happens to ask.
    """
    poll = {"seconds": first}
    monkeypatch.setattr(
        utils, "_remaining_seconds_or_state", lambda *_a, **_k: poll["seconds"]
    )
    return poll


def test_charging_finish_time_holds_a_dither_across_a_grid_boundary(
    monkeypatch,
) -> None:
    """Two estimates 2 minutes apart must not land in two different buckets.

    Measured on hardware over a 4.3 h session: 183 rows for 2 distinct values,
    the stamp alternating between 12:15 and 12:20 on consecutive polls. Snapping
    to a grid cannot fix that — an estimate sitting on a bucket edge flips every
    poll — so the estimate is held until it moves by a full step.
    """
    sensor = _soc_sensor(ChargingFinishTimeSensor, "1", powerMeas="7000")
    _freeze(monkeypatch, datetime(2026, 8, 28, 12, 0, tzinfo=dt_util.UTC))
    poll = _feed_seconds(monkeypatch, 7490)

    first = sensor._get_sensor_value()
    poll["seconds"] = 7610
    second = sensor._get_sensor_value()

    assert second == first


def test_time_to_target_holds_a_dither_across_a_grid_boundary(monkeypatch) -> None:
    """Same defect, same session: 2h 05m <-> 2h 10m on consecutive polls."""
    sensor = _soc_sensor(TimeToTargetSocSensor, "1", powerMeas="7000")
    poll = _feed_seconds(monkeypatch, 7350)

    first = sensor._get_sensor_value()
    poll["seconds"] = 7580
    second = sensor._get_sensor_value()

    assert second == first


def test_current_holds_the_two_tenth_dither_seen_all_session() -> None:
    """15.6/15.7/15.8/15.9 all session: a 0.2 A step must not be a row.

    The old 0.2 A band let exactly that swing through, because the reading is
    compared with `abs(value - last) < deadband` and 15.9 - 15.7 lands a hair
    ABOVE 0.2 in binary floating point.
    """
    updater = _updater({})

    assert _read(sd.get_current, updater, "curMeas1", [15.7, 15.9, 15.6, 15.8]) == (
        pytest.approx([15.7, 15.7, 15.7, 15.7])
    )


# --- Session Time: the charger counts from plug-in, not from charge start ---


def _session(seconds: int, state: int, updater=None):
    updater = updater or _updater({})
    updater.data.update({"sessionTime": seconds, "state": state})
    return updater


def test_session_time_still_states_every_minute_while_charging() -> None:
    """The one state where the running minute is what the user is watching."""
    updater = _session(3600, 4)
    assert sd.get_session_time(updater, None) == "1h 00m"

    _session(3660, 4, updater)
    assert sd.get_session_time(updater, None) == "1h 01m"


def test_session_time_states_five_minute_steps_once_charging_ends() -> None:
    """The charger's counter runs until the cable comes out, not until the
    charge finishes: a car left plugged in overnight after Charge Complete
    wrote a row every minute for a figure nobody is reading."""
    updater = _session(15600, 5)
    baseline = sd.get_session_time(updater, None)

    _session(15660, 5, updater)
    assert sd.get_session_time(updater, None) == baseline

    _session(15900, 5, updater)
    assert sd.get_session_time(updater, None) != baseline


def test_session_time_never_counts_backwards_when_charging_ends() -> None:
    """Charging states the minute, standby states the five — so the coarser
    step must not drag an already-published time back down."""
    updater = _session(15780, 4)
    assert sd.get_session_time(updater, None) == "4h 23m"

    # The five-minute floor of the very next reading is 4h 20m — three minutes
    # BEHIND what the charge already published.
    _session(15790, 5, updater)
    assert sd.get_session_time(updater, None) == "4h 23m"


def test_session_time_attribute_follows_the_state_it_mirrors() -> None:
    """An attribute writes a row exactly like a state does."""
    updater = _session(15600, 5)
    baseline = sd.get_session_time_attrs(updater, None)["duration_seconds"]

    _session(15660, 5, updater)

    assert sd.get_session_time_attrs(updater, None)["duration_seconds"] == baseline


def test_session_time_restarts_cleanly_when_the_cable_comes_out() -> None:
    """Unplugging resets the charger's counter; the held value must not stick."""
    updater = _session(15600, 5)
    sd.get_session_time(updater, None)

    _session(120, 4, updater)

    assert sd.get_session_time(updater, None) == "2m"
