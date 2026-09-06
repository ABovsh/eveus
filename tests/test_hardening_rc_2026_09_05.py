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
    _ESTIMATE_STEP_MINUTES,
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


# --- Every phase takes the step its own comment says it takes ---


@pytest.mark.parametrize(
    ("getter", "key"),
    [
        (sd.get_current_phase_2, "curMeas2"),
        (sd.get_current_phase_3, "curMeas3"),
    ],
)
def test_current_phases_take_the_same_step_as_phase_one(getter, key) -> None:
    """Phases 2 and 3 are the same telemetry, so they take the same step.

    Compared against phase 1 on the same feed rather than against a hardcoded
    swing: a swing chosen for one band silently stops discriminating when the
    band moves, and phase 2/3 keeping a narrower band than phase 1 has shipped
    once already. This holds whatever the band is set to.
    """
    feed = [15.7, 15.9, 15.6, 15.8, 16.1, 15.9, 12.0]

    assert _read(getter, _updater({}), key, feed) == pytest.approx(
        _read(sd.get_current, _updater({}), "curMeas1", feed)
    )


# --- The optimistic value must outrank a stale device reading ---


def test_setpoint_number_optimistic_value_outranks_a_stale_device_reading() -> None:
    """The largest control family had no guard on the rule it depends on.

    A setpoint written by the user is shown immediately and held for the
    optimistic TTL, because the charger keeps reporting the OLD number until it
    has applied the new one. `EveusCurrentNumber` has had this precedence
    pinned since the optimistic layer landed; the setpoint family — Energy and
    Cost Limit, every schedule limit, the Undervoltage threshold — never got
    the equivalent, so reversing the two reads left the whole suite green while
    every one of those sliders snapped back to the stale value after a write.
    """
    from unittest.mock import AsyncMock, MagicMock

    from custom_components.eveus.number import (
        EveusSetpointNumber,
        EveusSetpointNumberDescription,
    )

    description = EveusSetpointNumberDescription(
        key="limit_energy",
        name="Limit Energy",
        command="energyLimit",
        state_key="energyLimit",
        device_to_ha=1.0,
        ha_to_device=1000.0,
        native_min_value=0.0,
        native_max_value=100.0,
        native_step=1.0,
        native_unit_of_measurement="kWh",
    )
    updater = MagicMock()
    updater.available = True
    updater.data = {"energyLimit": 10}
    updater.send_command = AsyncMock(return_value=True)
    updater.config_entry = MagicMock()
    entity = EveusSetpointNumber(updater, description, device_number=1)
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()

    # The charger still reports the old figure, as it does until it applies the
    # write — so the two sources disagree, which is the only state in which the
    # precedence is observable at all.
    import asyncio

    asyncio.run(entity.async_set_native_value(40))
    assert updater.data["energyLimit"] == 10

    assert entity._resolve_value() == 40.0


# --- The damped minute count must respect the display floor the raw one had ---


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


# --- The grid is applied ONCE, not twice ---


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
