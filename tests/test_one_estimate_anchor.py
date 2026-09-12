"""The two charge estimates are two views of ONE damped quantity.

Charging Finish Time and Time to Target SOC answer the same question — when
does this charge end — so at any instant `now + Time to Target` must equal
Charging Finish Time. They used to be damped separately, and that is the whole
bug: one held an INSTANT (14:00 is still 14:00 five minutes later) while the
other held a DURATION ("120 minutes" silently means a finish time that slides
one minute later every minute). Between re-anchors nothing tied them together.

Measured on the real code before this change, on a two-hour charge at perfectly
steady power: Time to Target held "2h 00m" for seven and a half minutes and
then dropped straight to "1h 50m" — it could never state 1h 55m at all, only
seven of the twenty-five grid values the charge passes through — while the two
sensors disagreed by anywhere from -2.5 to +7.0 minutes on a repeating
sawtooth. On a ten-hour charge the spread was -2.5 to +17.0 minutes.

The fix is structural rather than a wider band: keep ONE anchor, the damped
finish instant, and project Time to Target off it. The disagreement is then a
single subtraction's rounding, permanently, and cannot be reintroduced by a
later change to only one of the two sensors.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from conftest import EV_HELPERS, EveusTestUpdater

from homeassistant.util import dt as dt_util

from custom_components.eveus import ev_sensors, utils
from custom_components.eveus.ev_sensors import (
    _ESTIMATE_STEP_MINUTES,
    _SUB_MINUTE_SECONDS,
    CachedSOCCalculator,
    ChargingFinishTimeSensor,
    TimeToTargetSocSensor,
)

START = datetime(2026, 9, 5, 12, 0, tzinfo=dt_util.UTC)
POLL_SECONDS = 30


def _pair(monkeypatch, remaining: float):
    """Both estimates on ONE updater, the way a real charger runs them.

    Returns (finish, eta, poll) where `poll` drives both the frozen clock and
    the single calculation the two sensors resolve through.
    """
    calculator = CachedSOCCalculator()
    for key, entity in (
        ("initial_soc", "input_number.ev_initial_soc"),
        ("battery_capacity", "input_number.ev_battery_capacity"),
        ("soc_correction", "input_number.ev_soc_correction"),
        ("target_soc", "input_number.ev_target_soc"),
    ):
        calculator.set_value(key, EV_HELPERS[entity])
    updater = EveusTestUpdater(
        {"sessionEnergy": "1", "state": 4, "powerMeas": "7000"}
    )
    poll = {"seconds": remaining, "now": START}
    monkeypatch.setattr(ev_sensors.dt_util, "utcnow", lambda: poll["now"])
    monkeypatch.setattr(
        utils, "_remaining_seconds_or_state", lambda *_a, **_k: poll["seconds"]
    )
    return (
        ChargingFinishTimeSensor(updater, 1, calculator),
        TimeToTargetSocSensor(updater, 1, calculator),
        poll,
    )


def _stated_minutes(text: str) -> int:
    """The minute count behind a "2h 05m" / "45m" / "5m" display string."""
    if "h " in text:
        hours, _, minutes = text.partition("h ")
        return int(hours) * 60 + int(minutes.rstrip("m"))
    return int(text.rstrip("m"))


def _steady_charge(monkeypatch, remaining: float, minutes: int):
    """Poll both sensors through `minutes` of a charge at unwavering power.

    Steady power is the point: every wobble below is the damping design's own,
    not the charger's.
    """
    finish, eta, poll = _pair(monkeypatch, remaining)
    trace = []
    for step in range(minutes * 60 // POLL_SECONDS + 1):
        poll["now"] = START + timedelta(seconds=POLL_SECONDS * step)
        poll["seconds"] = remaining - POLL_SECONDS * step
        stamp = finish._get_sensor_value()
        stated = _stated_minutes(eta._get_sensor_value())
        trace.append((poll["now"], stamp, stated))
    return finish, eta, trace


# --- The invariant the whole design exists to serve ---


@pytest.mark.parametrize(
    ("remaining", "minutes"),
    [(2 * 3600, 60), (10 * 3600, 120)],
    ids=["two-hour charge", "ten-hour charge"],
)
def test_the_two_estimates_agree_at_every_poll_not_just_the_first(
    monkeypatch, remaining: int, minutes: int
) -> None:
    """`now + Time to Target` must BE Charging Finish Time, all charge long.

    The pre-existing sibling check builds a fresh pair for each remaining time,
    so it only ever compares them at the instant they both anchor — the one
    moment two independent anchors are guaranteed to agree. Held across a real
    charge, they disagreed by up to 7 minutes on a two-hour charge and 17 on a
    ten-hour one. One anchor leaves only the projection's own rounding: half a
    grid step.
    """
    _, _, trace = _steady_charge(monkeypatch, remaining, minutes)
    worst = max(
        abs(((now + timedelta(minutes=stated)) - stamp).total_seconds() / 60)
        # The final grid step is excluded on purpose: there the duration is
        # pinned to its never-below-5m display floor while the stamp states the
        # boundary exactly. That floor predates the damping and is covered by
        # `test_the_display_floor_is_the_only_place_the_two_may_part`.
        for now, stamp, stated in trace
        if (stamp - now).total_seconds() > _ESTIMATE_STEP_MINUTES * 60
    )
    assert worst <= _ESTIMATE_STEP_MINUTES / 2, (
        f"the two estimates disagree by {worst:.1f} min, more than the "
        f"{_ESTIMATE_STEP_MINUTES / 2:.1f} min a single rounding allows"
    )


def test_time_to_target_counts_down_through_every_grid_step(monkeypatch) -> None:
    """The countdown must use the grid it states, not every second value of it.

    Damping a duration freezes the number while the clock runs, so the sensor
    stalled for seven and a half minutes and then jumped a full ten — landing
    only on 2h00, 1h50, 1h40 and never once on 1h55. Half of the five-minute
    grid was unreachable.
    """
    _, _, trace = _steady_charge(monkeypatch, 2 * 3600, 60)
    stated = [minutes for _, _, minutes in trace]
    assert {115, 105, 95, 85, 75} <= set(stated), (
        "Time to Target skips the odd five-minute steps: it showed "
        f"{sorted(set(stated), reverse=True)}"
    )
    # And it only ever counts DOWN — a projection off a held instant cannot
    # step back up the way a re-anchored duration could.
    assert stated == sorted(stated, reverse=True)


def test_the_finish_stamp_still_sits_still_while_power_is_steady(
    monkeypatch,
) -> None:
    """The half that already worked must keep working.

    Charging Finish Time wrote zero recorder rows across an hour of steady
    charging before this change; projecting Time to Target off it must not cost
    the stamp its stability, because the stamp is what an automation waits on.
    """
    _, _, trace = _steady_charge(monkeypatch, 2 * 3600, 60)
    stamps = {stamp for _, stamp, _ in trace}
    assert len(stamps) == 1, f"the stamp moved {len(stamps) - 1} times: {stamps}"


def test_only_one_anchor_is_kept_for_both_sensors(monkeypatch) -> None:
    """Two anchors is the defect; one is the fix, and it is observable.

    A second anchor added later — for a third view of the same estimate, say —
    would reintroduce exactly the drift this file exists to prevent.
    """
    finish, eta, _ = _pair(monkeypatch, 2 * 3600)
    finish._get_sensor_value()
    eta._get_sensor_value()
    assert len(finish._updater._estimate_anchors) == 1


def test_both_sensors_answer_the_same_whichever_reads_first(monkeypatch) -> None:
    """A shared anchor must not depend on which entity Home Assistant updates
    first: whoever gets there creates it, and the other must find it rather
    than lay down a second one of its own."""
    finish, eta, poll = _pair(monkeypatch, 2 * 3600)
    eta_first = eta._get_sensor_value()
    finish_first = finish._get_sensor_value()

    finish2, eta2, poll2 = _pair(monkeypatch, 2 * 3600)
    poll2["now"] = poll["now"]
    assert finish2._get_sensor_value() == finish_first
    assert eta2._get_sensor_value() == eta_first


# --- The lifecycle rules the old pair of anchors already had ---


def test_the_anchor_is_dropped_when_the_estimate_goes_away(monkeypatch) -> None:
    """A session that ends must not seed the next one with its held instant.

    With two anchors this rule had to be re-proved on each of them, and it
    failed on one: Time to Target dropped its anchor for free by routing a
    `None` through its damper, while the finish stamp returned early and left
    the previous session's instant in place. One anchor and one exit make that
    asymmetry unrepresentable.
    """
    finish, eta, poll = _pair(monkeypatch, 2 * 3600)
    finish._get_sensor_value()
    assert finish._updater._estimate_anchors

    poll["seconds"] = None
    assert finish._get_sensor_value() is None
    assert not finish._updater._estimate_anchors


def test_the_anchor_is_dropped_when_the_soc_inputs_go_away(monkeypatch) -> None:
    """The other half of the same rule, entered through the earlier exit.

    The path where the SOC inputs themselves disappear returns before any
    estimate is computed, so it needs an explicit drop rather than getting one
    for free from the damper.
    """
    finish, eta, _ = _pair(monkeypatch, 2 * 3600)
    eta._get_sensor_value()
    assert eta._updater._estimate_anchors

    eta._soc_calculator.set_value("battery_capacity", None)
    assert eta._get_sensor_value() is None
    assert not eta._updater._estimate_anchors


def test_a_genuine_change_still_moves_both_estimates(monkeypatch) -> None:
    """Damping is not freezing: power really halving must reach both sensors."""
    finish, eta, poll = _pair(monkeypatch, 2 * 3600)
    stamp, stated = finish._get_sensor_value(), eta._get_sensor_value()

    poll["seconds"] = 4 * 3600
    assert finish._get_sensor_value() != stamp
    assert eta._get_sensor_value() != stated


def test_the_display_floor_is_the_only_place_the_two_may_part(monkeypatch) -> None:
    """Inside the last grid step the duration reads "5m" and the stamp does not.

    That is the pre-damping promise `calculate_remaining_time` makes in its own
    comment — "under 5 minutes the sensor still reads 5m until it drops below
    one minute" — and the projection must not quietly repeal it by rendering
    "< 1m" for a charge with two minutes left. Below the minute the floor lifts
    and the honest answer comes back.
    """
    finish, eta, poll = _pair(monkeypatch, 2 * 3600)
    poll["seconds"] = 120
    assert eta._get_sensor_value() == "5m"
    assert finish._get_sensor_value() > poll["now"]

    poll["seconds"] = 20
    assert eta._get_sensor_value() == "< 1m"


def test_the_stamp_never_falls_into_the_past(monkeypatch) -> None:
    """A held instant that has come due is replaced, not published stale."""
    finish, _, poll = _pair(monkeypatch, 2 * 3600)
    finish._get_sensor_value()

    for step in range(1, 400):
        poll["now"] = START + timedelta(seconds=POLL_SECONDS * step)
        poll["seconds"] = 90  # a minute and a half, from here to the end
        stamp = finish._get_sensor_value()
        assert stamp > poll["now"], f"stamp {stamp} is not in the future"


# --- The band: what it absorbs, and what it must let through ---


def _moves(monkeypatch, remaining: float, to: float) -> bool:
    """Does the stamp change when the estimate jumps from `remaining` to `to`?"""
    finish, _, poll = _pair(monkeypatch, remaining)
    before = finish._get_sensor_value()
    poll["seconds"] = to
    return finish._get_sensor_value() != before


@pytest.mark.parametrize(
    ("remaining", "jump", "publishes", "why"),
    [
        # Short charge: the flat floor of one and a half grid steps rules.
        (2 * 3600, 300, False, "5 min is inside the 7.5 min floor"),
        (2 * 3600, 600, True, "10 min is past it"),
        # Long charge: 3 % of the time remaining overtakes the flat floor.
        (10 * 3600, 600, False, "10 min is inside 3 % of ten hours (18 min)"),
        (10 * 3600, 1500, True, "25 min is past it"),
    ],
)
def test_the_band_is_the_wider_of_a_flat_floor_and_a_share_of_the_estimate(
    monkeypatch, remaining: int, jump: int, publishes: bool, why: str
) -> None:
    """Both halves of `max(step * 1.5, fraction * seconds)` have to bite.

    Only the long-charge pair can see the proportional half at all — on a short
    charge the flat floor is always the larger — so a band that dropped the
    fraction, or divided by the estimate instead of scaling with it, would look
    correct everywhere except on an overnight charge, which is precisely where
    the same power swing moves the answer furthest.
    """
    assert _moves(monkeypatch, remaining, remaining - jump) is publishes, why


def test_a_move_exactly_the_width_of_the_band_publishes(monkeypatch) -> None:
    """`>=` and not `>`: the boundary itself is a real move.

    Stated rather than left to chance because it is the one comparison whose
    two forms differ on exactly one value, and a band is a rounding decision —
    silently taking the other side of it costs a row the user was owed, or
    keeps one they were not.
    """
    remaining = 2 * 3600
    band = _ESTIMATE_STEP_MINUTES * 60 * 1.5

    finish, _, poll = _pair(monkeypatch, remaining)
    before = finish._get_sensor_value()
    # The anchor is snapped to the grid, so measure the move from the anchor
    # rather than from the raw estimate it came from.
    held = finish._updater._estimate_anchors["finish_at"]
    poll["seconds"] = held - START.timestamp() - band

    assert finish._get_sensor_value() != before


def test_the_sub_minute_threshold_is_thirty_seconds(monkeypatch) -> None:
    """The exact second at which the display floor lifts.

    Half a minute is where `round(seconds / 60)` turns zero, which is the rule
    `calculate_remaining_time` has always used. Pinning the boundary is what
    stops the threshold drifting into the floor's territory, where a charge
    with a minute left would read "< 1m", or out of it, where one with seconds
    left would read "5m".

    The constant is pinned to the PROPERTY it claims rather than to its digits:
    it must be the last second that rounds to zero minutes. Any larger value
    happens to behave the same today — above half a minute both branches end at
    the same "5m", because `calculate_remaining_time` applies the same floor —
    so only the lower edge is load-bearing, and only this assertion sees it.
    """
    assert round(_SUB_MINUTE_SECONDS / 60) == 0
    assert round((_SUB_MINUTE_SECONDS + 1) / 60) == 1

    _, eta, poll = _pair(monkeypatch, 2 * 3600)

    poll["seconds"] = 30
    assert eta._get_sensor_value() == "< 1m"

    poll["seconds"] = 31
    assert eta._get_sensor_value() == "5m"
