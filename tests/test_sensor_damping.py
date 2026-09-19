"""Sensor churn damping and charge-estimate holds: deadbands cover every
dithering reading, and an estimate hold never outlives what it damps."""
from __future__ import annotations

import pytest
from types import SimpleNamespace
from conftest import PayloadUpdater
from custom_components.eveus import sensor_definitions as sd
from test_soc_autofill import _build, _poll  # noqa: F401  (fixtures come along)
from test_soc_autofill import _no_dispatcher  # noqa: F401
from datetime import datetime, timedelta
from conftest import EV_HELPERS, EveusTestUpdater
from homeassistant.util import dt as dt_util
from custom_components.eveus import ev_sensors
from custom_components.eveus import utils
from custom_components.eveus.ev_sensors import (
    _ESTIMATE_STEP_MINUTES,
    CachedSOCCalculator,
    ChargingFinishTimeSensor,
    TimeToTargetSocSensor,
)


def test_value_getter_rejects_overflow_error():
    """Regression test for B01: float() on an absurdly large int raises
    OverflowError, not TypeError/ValueError — the coercion must catch it too,
    matching every other numeric getter in this module.
    """
    from conftest import EveusTestUpdater
    from custom_components.eveus.sensor_definitions import _make_value_getter

    getter = _make_value_getter("powerMeas")
    updater = EveusTestUpdater({"powerMeas": 10**400})

    assert getter(updater, None) is None


def test_connection_attrs_isolates_wifi_rssi_failure():
    """Regression test for B02: a failure fetching the optional wifi_rssi must
    only drop that one field, not replace the whole (already-valid)
    connection_quality/latency_avg/status dict with {"status": "Error"}.

    Here the updater carries no payload at all, so reading RSSI fails; that
    must be just as harmless to the rest of the dict.
    """
    from types import SimpleNamespace

    from custom_components.eveus import sensor_definitions as sd

    updater = SimpleNamespace(
        available=True, connection_quality={"success_rate": 75, "latency_avg": 0.42}
    )

    attrs = sd.get_connection_attrs(updater, None)

    assert attrs["status"] == "Fair"
    assert attrs["connection_quality"] == 75
    assert "wifi_rssi" not in attrs

def _updater(data: dict[str, object]) -> SimpleNamespace:
    return PayloadUpdater(data, host="192.168.1.50")


def _spec(key: str, phases: int = 1) -> sd.EveusSensorEntityDescription:
    return next(s for s in sd.create_sensor_specifications(phases=phases) if s.key == key)


def _read(spec_key: str, updater, key: str, values, phases: int = 1) -> list:
    """Feed successive payload values through one entity's own deadband."""
    sensor = sd.create_sensor(_spec(spec_key, phases=phases), updater, 1)
    out = []
    for value in values:
        updater.data[key] = value
        sensor._update_native_value()
        out.append(sensor._attr_native_value)
    return out


@pytest.mark.parametrize(
    ("spec_key", "key", "feed", "expected", "phases"),
    [
        # Phases 2 and 3 are the same telemetry as phase 1 on a 3-phase entry,
        # so they dither the same way and take the same step.
        ("voltage_phase_2", "voltMeas2", [230, 231, 229, 232], [230, 230, 230, 232], 3),
        ("voltage_phase_3", "voltMeas3", [230, 231, 229, 232], [230, 230, 230, 232], 3),
        ("current_phase_2", "curMeas2", [16.0, 16.1, 15.9, 16.3], [16.0, 16.0, 16.0, 16.3], 3),
        ("current_phase_3", "curMeas3", [16.0, 16.1, 15.9, 16.3], [16.0, 16.0, 16.0, 16.3], 3),
        # Whole-degree enclosure temperatures alternate between two adjacent
        # readings for hours; a 1 degree band would be no band at all, because
        # the next distinct value is already 1 away.
        ("box_temperature", "temperature1", [30, 31, 30, 32], [30, 30, 30, 32], 1),
        ("plug_temperature", "temperature2", [30, 31, 30, 32], [30, 30, 30, 32], 1),
    ],
)
def test_dithering_getters_hold_until_the_deadband_is_crossed(
    spec_key, key, feed, expected, phases
) -> None:
    assert _read(spec_key, _updater({}), key, feed, phases=phases) == pytest.approx(expected)


def test_wifi_rssi_holds_a_swing_the_link_quality_does_not_notice() -> None:
    """Measured on the live charger: RSSI wanders across ~7 dBm all day.

    A 3 dBm band still published one reading in seven; the link is "Excellent"
    across the whole swing, so the rows carried nothing.
    """
    assert _read("wifi_signal", _updater({}), "RSSI", [-66, -70, -69, -73]) == [
        -66,
        -66,
        -66,
        -73,
    ]

def _updater_09_05(data: dict[str, object], **extra) -> SimpleNamespace:
    fields: dict[str, object] = {
        "available": True,
        "connection_quality": {},
        "host": "192.168.1.50",
    }
    fields.update(extra)
    return PayloadUpdater(data, **fields)


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


@pytest.mark.parametrize(
    ("spec_key", "key"),
    [
        ("current_phase_2", "curMeas2"),
        ("current_phase_3", "curMeas3"),
    ],
)
def test_current_phases_take_the_same_step_as_phase_one(spec_key, key) -> None:
    """Phases 2 and 3 are the same telemetry, so they take the same step.

    Compared against phase 1 on the same feed rather than against a hardcoded
    swing: a swing chosen for one band silently stops discriminating when the
    band moves, and phase 2/3 keeping a narrower band than phase 1 has shipped
    once already. This holds whatever the band is set to.
    """
    feed = [15.7, 15.9, 15.6, 15.8, 16.1, 15.9, 12.0]

    assert _read(spec_key, _updater_09_05({}), key, feed, phases=3) == pytest.approx(
        _read("current", _updater_09_05({}), "curMeas1", feed)
    )


@pytest.mark.parametrize("seconds", [60, 120, 150])
def test_time_to_target_never_states_under_a_minute_while_minutes_remain(
    monkeypatch, seconds: int
) -> None:
    """A small estimate must not be damped down to "< 1m".

    ``calculate_remaining_time`` promises in its own comment that it "never
    rounds down to 0m — under 5 minutes the sensor still reads 5m until it
    drops below one minute". The damper snaps the minute count onto the
    five-minute grid BEFORE that function sees it, and ``round(2 / 5) * 5`` is
    zero — so a charge with two minutes left began reading "< 1m". It then
    stuck there for the rest of the charge, because the band is measured from
    the held zero and nothing under seven and a half minutes can cross it.
    """
    sensor = _soc_sensor(TimeToTargetSocSensor, "1", powerMeas="7000")
    poll = _feed_seconds(monkeypatch, seconds)

    first = sensor._get_sensor_value()
    assert first == "5m"

    # And it must not fall into "< 1m" on the polls that follow either.
    poll["seconds"] = seconds - 10
    assert sensor._get_sensor_value() == "5m"


def test_time_to_target_still_states_under_a_minute_below_the_minute(
    monkeypatch,
) -> None:
    """The floor is a floor, not a lie: a genuinely sub-minute estimate still
    reads "< 1m", exactly as the undamped path did."""
    sensor = _soc_sensor(TimeToTargetSocSensor, "1", powerMeas="7000")
    poll = _feed_seconds(monkeypatch, 120)
    assert sensor._get_sensor_value() == "5m"

    poll["seconds"] = 20
    assert sensor._get_sensor_value() == "< 1m"


def test_finish_stamp_does_not_overshoot_the_time_it_states(monkeypatch) -> None:
    """`_damped_estimate` already snaps the instant onto the five-minute grid.

    Feeding that grid-aligned instant into the older unconditional "snap UP to
    the next boundary" step adds a further full step every time, because an
    aligned minute always has `minute % 5 == 0`. The stamp then sits up to one
    and a half steps beyond the estimate it is supposed to state, and the two
    charge estimates — which the design says are two views of one calculation —
    drift apart systematically rather than by rounding.
    """
    moment = datetime(2026, 9, 5, 12, 0, tzinfo=dt_util.UTC)
    for remaining in range(3600, 6 * 3600, 337):
        sensor = _soc_sensor(ChargingFinishTimeSensor, "1", powerMeas="7000")
        _freeze(monkeypatch, moment)
        _feed_seconds(monkeypatch, remaining)
        stamp = sensor._get_sensor_value()
        overshoot = (stamp - moment).total_seconds() - remaining
        assert overshoot <= _ESTIMATE_STEP_MINUTES * 60, (
            f"{remaining}s remaining: stamp overshoots by {overshoot / 60:.1f} min, "
            f"more than the one {_ESTIMATE_STEP_MINUTES}-minute step the grid allows"
        )
        assert stamp > moment, "the stamp must stay in the future"


def test_both_estimates_state_the_same_time_not_just_the_same_band(
    monkeypatch,
) -> None:
    """The stronger half of the sibling invariant.

    The existing guard compares WHICH polls moved; it cannot see the two
    estimates agreeing on when to move while disagreeing on the answer.
    """
    moment = datetime(2026, 9, 5, 12, 0, tzinfo=dt_util.UTC)
    for remaining in (3600, 5000, 7200, 9999, 14400):
        finish = _soc_sensor(ChargingFinishTimeSensor, "1", powerMeas="7000")
        eta = _soc_sensor(TimeToTargetSocSensor, "1", powerMeas="7000")
        _freeze(monkeypatch, moment)
        _feed_seconds(monkeypatch, remaining)
        stamp_minutes = (finish._get_sensor_value() - moment).total_seconds() / 60
        text = eta._get_sensor_value()
        hours, _, mins = text.partition("h ")
        stated = int(hours) * 60 + int(mins.rstrip("m")) if mins else int(
            text.rstrip("m")
        )
        assert abs(stamp_minutes - stated) <= _ESTIMATE_STEP_MINUTES, (
            f"{remaining}s remaining: finish stamp says {stamp_minutes:.0f} min, "
            f"Time to Target says {stated} min"
        )
