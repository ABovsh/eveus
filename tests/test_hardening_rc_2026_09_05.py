"""Charge-estimate damping: the hold must not outlive what it is damping.

The estimates are damped so a power reading that wanders does not rewrite the
same figure on every poll. A hold that is too wide stops being damping and
becomes a freeze: the sensor keeps publishing the estimate it made at the
start of the session while the real one moves hours away from it.

Both estimates are two views of ONE calculation, so they take the same band
for the same remaining time — that is the invariant the grid exists to serve.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from conftest import EV_HELPERS, EveusTestUpdater
from types import SimpleNamespace

from homeassistant.util import dt as dt_util

from custom_components.eveus import ev_sensors
from custom_components.eveus import utils
from custom_components.eveus import sensor_definitions as sd
from custom_components.eveus.ev_sensors import (
    CachedSOCCalculator,
    ChargingFinishTimeSensor,
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


def _soc_sensor(cls, session_energy: str, **payload):
    data = {"sessionEnergy": session_energy, "state": 4, "powerMeas": "7000"}
    data.update(payload)
    return cls(EveusTestUpdater(data), 1, _push(CachedSOCCalculator()))


def _freeze(monkeypatch, moment: datetime) -> None:
    monkeypatch.setattr(ev_sensors.dt_util, "utcnow", lambda: moment)


def _feed_seconds(monkeypatch, first: float) -> dict:
    """Drive the one calculation both estimates resolve through."""
    poll = {"seconds": first}
    monkeypatch.setattr(
        utils, "_remaining_seconds_or_state", lambda *_a, **_k: poll["seconds"]
    )
    return poll


def _read(getter, updater, key: str, values) -> list:
    out = []
    for value in values:
        updater.data[key] = value
        out.append(getter(updater, None))
    return out


# --- The hold must still let a genuine change through ---


def test_charging_finish_time_follows_a_real_decline(monkeypatch) -> None:
    """The sibling of test_estimate_still_follows_a_real_decline.

    Time to Target SOC has had this test since the damping landed; Charging
    Finish Time never got one, which is exactly why its band could grow to
    cover a whole session unnoticed. Halving the charge rate doubles the time
    left — a change no band may absorb.
    """
    sensor = _soc_sensor(ChargingFinishTimeSensor, "1", powerMeas="7000")
    _freeze(monkeypatch, datetime(2026, 9, 5, 12, 0, tzinfo=dt_util.UTC))
    poll = _feed_seconds(monkeypatch, 2 * 3600)

    first = sensor._get_sensor_value()
    poll["seconds"] = 4 * 3600
    second = sensor._get_sensor_value()

    assert second != first
    assert second - first >= timedelta(hours=1, minutes=45)


def test_both_estimates_take_the_same_band_for_the_same_remaining_time(
    monkeypatch,
) -> None:
    """Two views of one calculation, so one band — this is the whole design.

    A move the finish stamp absorbs must be one Time to Target also absorbs,
    and a move one of them publishes must be published by both. Feeding the
    identical series to each and comparing WHICH polls moved catches a band
    that is measured against the wrong magnitude, whatever its size.
    """
    moment = datetime(2026, 9, 5, 12, 0, tzinfo=dt_util.UTC)
    series = [8 * 3600, 8 * 3600 + 120, 8 * 3600 - 90, 6 * 3600, 3 * 3600]

    def moved(cls) -> list[bool]:
        sensor = _soc_sensor(cls, "1", powerMeas="7000")
        _freeze(monkeypatch, moment)
        poll = _feed_seconds(monkeypatch, series[0])
        seen = [sensor._get_sensor_value()]
        for seconds in series[1:]:
            poll["seconds"] = seconds
            seen.append(sensor._get_sensor_value())
        return [b != a for a, b in zip(seen, seen[1:])]

    assert moved(ChargingFinishTimeSensor) == moved(TimeToTargetSocSensor)


def test_charging_finish_time_forgets_its_anchor_when_the_estimate_goes_away(
    monkeypatch,
) -> None:
    """A session that ends must not seed the next one.

    Time to Target routes its `None` through the damper, which drops the
    anchor. The finish stamp returned early instead, leaving the previous
    session's held instant in place for the next one to inherit.
    """
    sensor = _soc_sensor(ChargingFinishTimeSensor, "1", powerMeas="7000")
    _freeze(monkeypatch, datetime(2026, 9, 5, 12, 0, tzinfo=dt_util.UTC))
    poll = _feed_seconds(monkeypatch, 2 * 3600)
    sensor._get_sensor_value()
    assert "finish_time" in sensor._updater._estimate_anchors

    poll["seconds"] = None
    assert sensor._get_sensor_value() is None

    assert "finish_time" not in sensor._updater._estimate_anchors


# --- Every phase takes the step its own comment says it takes ---


@pytest.mark.parametrize(
    ("getter", "key"),
    [
        (sd.get_current_phase_2, "curMeas2"),
        (sd.get_current_phase_3, "curMeas3"),
    ],
)
def test_current_phases_hold_the_same_swing_phase_one_holds(getter, key) -> None:
    """Phases 2 and 3 are the same telemetry, so they take the same step.

    The existing parametrised case only proves SOME band is present: its feed
    crosses at 0.2 A and at 0.25 A alike, so the widening phase 1 received
    could be reverted on these two with the suite still green. Phase 2/3
    silently keeping a narrower band than phase 1 has shipped once already.
    """
    assert _read(getter, _updater({}), key, [15.7, 15.9, 15.6, 15.8]) == (
        pytest.approx([15.7, 15.7, 15.7, 15.7])
    )
