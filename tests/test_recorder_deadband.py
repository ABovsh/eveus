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
from custom_components.eveus.const import MAX_SESSION_TIME_SECONDS
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


def test_power_deadband_boundary_is_exactly_fifty_watts() -> None:
    """A 50 W swing publishes; anything smaller holds — pins the exact band."""
    updater = _updater({})

    assert _read(sd.get_power, updater, "powerMeas", [3500, 3549, 3550]) == [
        3500,
        3500,
        3550,
    ]


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


def test_cost_to_target_deadband_boundary_is_exactly_one_uah() -> None:
    """At 4.00 UAH/kWh, a 0.25 kWh step is exactly 1 UAH and must publish."""
    sensor = _soc_sensor(CostToTargetSocSensor, "10", tarif="400", activeTarif="0")
    sensor._update_native_value()
    baseline = sensor._attr_native_value
    assert baseline is not None

    sensor._updater.data["sessionEnergy"] = "10.25"
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


def test_current_holds_a_tenth_of_an_amp_from_any_anchor() -> None:
    """The 0.2 A band holds the 0.1 A step, wherever the last publish anchored.

    `apply_deadband` compares strictly, so 0.2 A is the first move that
    publishes and 0.1 A never is — and that has to hold from every anchor the
    swing can leave behind, not just from its middle, because publishing
    re-anchors on the value published. Live over a fortnight the reading sits
    on 15.6/15.7/15.8/15.9, so each of those is a reachable anchor.
    """
    for anchor in (15.6, 15.7, 15.8, 15.9):
        neighbours = [round(anchor + step, 1) for step in (0.1, -0.1, 0.1)]
        updater = _updater({})
        assert _read(
            sd.get_current, updater, "curMeas1", [anchor, *neighbours]
        ) == pytest.approx([anchor] * 4), f"0.1 A step published from {anchor}"


def test_current_publishes_a_two_tenth_move_by_design() -> None:
    """0.2 A is the band, and a move equal to the band is a row.

    Pinned so the boundary cannot drift silently: the charger reports current
    to 0.1 A, so this is the smallest move the sensor is allowed to record.
    """
    updater = _updater({})

    assert _read(sd.get_current, updater, "curMeas1", [15.7, 15.9]) == pytest.approx(
        [15.7, 15.9]
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


def test_time_to_target_holds_the_jitter_measured_on_hardware(monkeypatch) -> None:
    """A band the size of the grid step is crossed by the swing it must absorb.

    Measured on the charger: power wanders 3504-3537 W all session, which moves
    an eight-hour estimate about five minutes peak to peak -- the same size as
    the band. So the band opens on the extremes, and because it re-anchors on
    the RAW estimate it re-anchors on a peak, leaving the opposite peak a full
    swing away and crossing again on the next poll. The recorder showed the
    result: 8h 05m <-> 8h 10m <-> 8h 15m, 26 rows for 19 values in three hours.
    A band has to be wider than the noise it absorbs, and measured from the
    value that was actually published.
    """
    sensor = _soc_sensor(TimeToTargetSocSensor, "1", powerMeas="7000")
    poll = _feed_seconds(monkeypatch, 492 * 60)

    seen = [sensor._get_sensor_value()]
    for minutes in (487, 492, 487, 492, 488):
        poll["seconds"] = minutes * 60
        seen.append(sensor._get_sensor_value())

    assert seen == [seen[0]] * len(seen)


def test_charging_finish_time_holds_the_jitter_measured_on_hardware(
    monkeypatch,
) -> None:
    """Same session, same swing: 23:35 <-> 23:40 on alternating polls."""
    sensor = _soc_sensor(ChargingFinishTimeSensor, "1", powerMeas="7000")
    _freeze(monkeypatch, datetime(2026, 8, 29, 15, 21, tzinfo=dt_util.UTC))
    poll = _feed_seconds(monkeypatch, 492 * 60)

    seen = [sensor._get_sensor_value()]
    for minutes in (487, 492, 487, 492, 488):
        poll["seconds"] = minutes * 60
        seen.append(sensor._get_sensor_value())

    assert seen == [seen[0]] * len(seen)


def test_estimate_still_follows_a_real_decline() -> None:
    """Damping must not freeze the estimate: a genuine drop still lands."""
    sensor = _soc_sensor(TimeToTargetSocSensor, "1", powerMeas="7000")
    first = sensor._get_sensor_value()
    sensor._updater.data["powerMeas"] = "14000"
    sensor._soc_calculator._cache.clear() if hasattr(
        sensor._soc_calculator, "_cache"
    ) else None

    assert sensor._get_sensor_value() != first


# --- Session Time: the hold has to survive a restart, or it counts backwards --


def _session_time_sensor(updater):
    spec = next(
        s
        for s in sd.create_sensor_specifications(phases=1, max_current=16)
        if s.key == "session_time"
    )
    return spec.create_sensor(updater)


def _restored(updater, attributes: dict | None, state: str = "5d 21h 24m"):
    """Build the sensor and hand it the state HA kept from before the restart."""
    from homeassistant.core import State

    sensor = _session_time_sensor(updater)
    sensor._seed_session_hold(State("sensor.eveus_session_time", state, attributes))
    return sensor


def test_only_session_time_declares_a_hold_to_restore() -> None:
    """The wiring, so the behaviour below cannot pass on a sensor nobody builds.

    `isinstance(..., RestoreEntity)` proves nothing here — every Eveus entity is
    one. What matters is which spec asks for the seeding.
    """
    declared = {
        spec.key
        for spec in sd.create_sensor_specifications(phases=1, max_current=16)
        if spec.restores_session_hold
    }
    assert declared == {"session_time"}


def test_being_added_to_hass_seeds_the_hold(monkeypatch) -> None:
    """The override has to run on the real entity-add path, not just be callable."""
    import asyncio

    from homeassistant.core import State

    # Stub the COORDINATOR entity, not EveusSensorBase: the base's own
    # async_added_to_hass computes and caches the first value, and stubbing it
    # away is what let the seeding run one step too late without any test
    # noticing. Everything from BaseEveusEntity down now runs for real.
    from homeassistant.helpers.update_coordinator import CoordinatorEntity

    async def _no_coordinator_setup(self) -> None:
        return None

    monkeypatch.setattr(
        CoordinatorEntity, "async_added_to_hass", _no_coordinator_setup, raising=True
    )
    updater = _session(509095, 2)
    sensor = _session_time_sensor(updater)

    async def _last_state():
        return State("sensor.x", "5d 21h 24m", {"duration_seconds": 509040})

    sensor.async_get_last_state = _last_state
    asyncio.run(sensor.async_added_to_hass())

    # The hold itself, and — the part the stub used to hide — the value the
    # entity has ALREADY published by the time it finishes being added. A seed
    # that lands after the first computation fixes polls 2+ and leaves the
    # regression exactly where it was seen: on the first reading after a reload.
    assert sd.get_session_time(updater, None) == "5d 21h 24m"
    assert sensor.native_value == "5d 21h 24m"


def test_session_time_hold_survives_a_restart() -> None:
    """The hold lives on the updater, so a reload used to drop it.

    A charge that ended left the figure on the minute grid; the next reading
    after a restart takes the five-minute idle floor, which is up to 4:59
    BEHIND it. Measured live 2026-09-05: the charger reported 509 095 s
    (5d 21h 24m) and the sensor published 5d 21h 20m across a restart.
    """
    updater = _session(509095, 2)
    sensor = _restored(updater, {"duration_seconds": 509040})
    assert sensor is not None

    assert sd.get_session_time(updater, None) == "5d 21h 24m"


def test_a_restored_hold_never_outranks_a_counter_that_reset() -> None:
    """Unplugged while HA was down: the charger's own counter starts over and
    the restored hold must not resurrect the finished session."""
    updater = _session(120, 4)
    _restored(updater, {"duration_seconds": 509040})

    assert sd.get_session_time(updater, None) == "2m"


@pytest.mark.parametrize("restored", [-1, 10**9, "nonsense", None])
def test_an_unusable_restored_hold_is_ignored(restored) -> None:
    """A corrupt or out-of-range restored value must not become the floor."""
    updater = _session(509095, 2)
    _restored(updater, {"duration_seconds": restored})

    assert sd.get_session_time(updater, None) == "5d 21h 20m"


def test_a_restart_with_no_previous_state_changes_nothing() -> None:
    """First install, or a state HA could not keep."""
    updater = _session(509095, 2)
    sensor = _session_time_sensor(updater)
    sensor._seed_session_hold(None)

    assert sd.get_session_time(updater, None) == "5d 21h 20m"


def test_rssi_deadband_boundary_is_exactly_five_dbm() -> None:
    """A 5 dBm move publishes; 4 holds — pins the exact band.

    The parametrised dither case crosses at 5 and at 6 alike, so the band could
    be widened by a dBm with the suite still green. RSSI is mirrored into the
    Connection Quality attributes, which made it the single largest source of
    recorder rows this integration produced, so the band it actually takes is
    worth stating rather than inferring.
    """
    updater = _updater({})

    assert _read(sd.get_wifi_rssi, updater, "RSSI", [-66, -70, -71]) == [
        -66,  # anchor
        -66,  # 4 dBm: inside the band, held
        -71,  # 5 dBm: a move EQUAL to the band publishes
    ]


@pytest.mark.parametrize(
    ("restored", "accepted"),
    [
        (0, True),  # a session that has only just begun
        (MAX_SESSION_TIME_SECONDS, True),  # the longest one the getter allows
        (MAX_SESSION_TIME_SECONDS + 1, False),  # one second past it
    ],
)
def test_the_restored_hold_accepts_exactly_the_range_the_getter_does(
    restored, accepted
) -> None:
    """Both ends of `0 <= seconds <= MAX` are inclusive, and nothing past them.

    The corrupt-value cases sit far outside the range, so they cannot tell an
    inclusive bound from an exclusive one. These three can: a zero rejected
    would drop the hold on the poll after a plug-in, and a bound that stopped
    being inclusive at the top would reject the very value the getter itself
    still publishes — the seed would silently do nothing on the longest
    sessions, which are exactly the ones the hold exists for.
    """
    updater = _session(MAX_SESSION_TIME_SECONDS, 2)
    sensor = _restored(updater, {"duration_seconds": restored})

    seeded = getattr(sensor._updater, "_session_time_seconds", None)
    assert (seeded == restored) is accepted


def test_the_hold_only_applies_while_the_charger_is_still_ahead_of_it() -> None:
    """`stepped < last <= seconds`, and both comparisons are load-bearing.

    The middle term is the held figure. It may only stand while the charger's
    own counter has reached it — otherwise a cable pulled and replugged would
    inherit the old session's floor — and only while the coarser step would
    actually drag the display backwards. Equality on each side is the case that
    separates the operators: `last == stepped` is nothing to hold, and
    `last == seconds` is a counter that has exactly caught up, which must still
    hold rather than fall back a step.
    """
    # last == seconds exactly. 3660 s is a whole minute but NOT a whole five,
    # so the charging step holds it verbatim and the idle step would drop it to
    # 3600 — the one shape where the upper comparison's two forms disagree.
    updater = _session(3660, 4)
    assert sd.get_session_time(updater, None) == "1h 01m"

    updater.data["state"] = 2
    assert sd.get_session_time(updater, None) == "1h 01m", (
        "a counter that has exactly reached the hold must not fall back a step"
    )

    # And the hold is released the moment the coarse step catches up.
    updater.data["sessionTime"] = 3900
    assert sd.get_session_time(updater, None) == "1h 05m"


def test_latency_avg_holds_instead_of_flipping_across_its_rounding_boundary() -> None:
    """`latency_avg` must hold its last published step, not re-round every poll.

    Measured on live hardware 2026-09-12: the rolling poll latency sits right on
    the 0.25 s edge of the 0.5 s display grid, so a plain `round(x * 2) / 2`
    alternated 0.0 / 0.5 / 0.0 on consecutive polls. The state never moved off
    100, but an attribute change writes a recorder row exactly like a state
    change — 25 of the 56 eveus rows in a two-hour idle window were this one
    attribute dithering. Rounding is not a deadband: the fix has to hold
    against the LAST PUBLISHED step.
    """
    quality = {"success_rate": 100, "latency_avg": 0.24}
    updater = SimpleNamespace(available=True, data={}, connection_quality=quality)

    first = sd.get_connection_attrs(updater, None)["latency_avg"]

    # Dither across the boundary the way the hardware does, never moving a full step.
    for sample in (0.26, 0.24, 0.26, 0.24, 0.26):
        quality["latency_avg"] = sample
        assert sd.get_connection_attrs(updater, None)["latency_avg"] == first, (
            f"latency_avg republished on a {sample - 0.25:+.2f}s wobble around the "
            "grid edge — that is one recorder row per poll, forever"
        )

    # A genuine degradation of a full step still gets through.
    quality["latency_avg"] = first + 0.75
    assert sd.get_connection_attrs(updater, None)["latency_avg"] > first, (
        "a real latency increase must not be swallowed by the hold"
    )


def test_latency_avg_hold_is_per_updater() -> None:
    """Two chargers must not share one latency anchor."""
    slow = SimpleNamespace(
        available=True, data={}, connection_quality={"success_rate": 100, "latency_avg": 3.0}
    )
    fast = SimpleNamespace(
        available=True, data={}, connection_quality={"success_rate": 100, "latency_avg": 0.0}
    )

    assert sd.get_connection_attrs(slow, None)["latency_avg"] == 3.0
    assert sd.get_connection_attrs(fast, None)["latency_avg"] == 0.0
    assert sd.get_connection_attrs(slow, None)["latency_avg"] == 3.0


def test_a_non_dict_anchor_store_is_replaced_not_used() -> None:
    """A stand-in that answers every attribute must not poison a reading.

    `_deadband_anchor_store` is reached via `getattr(updater, ..., None)`, so
    anything that answers arbitrary attributes hands back a non-dict whose
    `.get()` returns a non-number. Doing arithmetic against that raises, and
    both callers report a raised getter as a failed reading — a valid latency
    or current would surface as `{"status": "Error"}` / `None` rather than the
    value the charger actually reported.
    """
    class _AnswersAnything:
        available = True
        data: dict = {}
        connection_quality = {"success_rate": 75, "latency_avg": 0.42}

        def __getattr__(self, name):  # pragma: no cover - mirrors a mock's behaviour
            return _AnswersAnything()

        def get(self, *_args):
            return _AnswersAnything()

    updater = _AnswersAnything()
    attrs = sd.get_connection_attrs(updater, None)

    assert attrs["status"] == "Fair", "a valid reading must survive a bogus anchor store"
    assert attrs["latency_avg"] == 0.5
    assert isinstance(updater._deadband_anchors, dict)


def test_the_latency_hold_does_not_latch_onto_a_cold_start_spike() -> None:
    """The first poll after a restart is the slowest, and must not become the answer.

    A cold start pays for connection setup, so the rolling average begins on an
    unrepresentative high sample and settles as the window fills. Anchoring on
    that first reading parks the published figure on the spike: measured on live
    hardware 2026-09-12, latency_avg held 0.5 s for the whole run while the
    charger was actually answering in 0.10-0.13 s. A hold that never revisits
    its own boundary reports a wrong value forever, which is worse than the
    churn it was added to remove — so while the sample window is still filling
    the figure tracks, and only then holds.
    """
    quality = {"success_rate": 100, "latency_avg": 3.0, "latency_samples": 1}
    updater = SimpleNamespace(available=True, data={}, connection_quality=quality)

    assert sd.get_connection_attrs(updater, None)["latency_avg"] == 3.0

    # The window fills with the charger's real response times; the average
    # settles and the published figure has to follow it down.
    for samples, avg in ((2, 1.55), (4, 0.8), (7, 0.4), (10, 0.13)):
        quality["latency_samples"] = samples
        quality["latency_avg"] = avg
        published = sd.get_connection_attrs(updater, None)["latency_avg"]

    assert published == 0.0, (
        f"a full window averaging {quality['latency_avg']}s still published "
        f"{published}s — the hold latched onto the cold-start spike"
    )

    # Once the window is full the hold engages: further dither is absorbed.
    for avg in (0.11, 0.30, 0.09, 0.28):
        quality["latency_avg"] = avg
        assert sd.get_connection_attrs(updater, None)["latency_avg"] == 0.0


_SOC_INPUTS = {
    "initial_soc": 20,
    "battery_capacity": 80,
    "soc_correction": 10,
    "target_soc": 80,
}


def _finish_sensor(state: int, helpers=_SOC_INPUTS, **updater_kw):
    """A Charging Finish Time sensor over a charger in the given device state."""
    calculator = CachedSOCCalculator()
    if helpers:
        for key, value in helpers.items():
            calculator.set_value(key, value)
    updater = EveusTestUpdater(
        {"state": state, "sessionEnergy": "16", "powerMeas": "7000"}, **updater_kw
    )
    return ChargingFinishTimeSensor(updater, 1, calculator)


def test_charging_finish_time_is_unavailable_not_unknown_between_charges() -> None:
    """A timestamp with no charge to finish must be `unavailable`, never blank.

    Time to Target has a "Not charging" string to fall back on; a
    `device_class=timestamp` entity has only a datetime or nothing, so its one
    honest blank is `unavailable` — the state helpers, statistics and templates
    SKIP — rather than `unknown`, which they ingest as a real but invalid
    reading. Seen on live hardware 2026-09-12: the sensor went `unknown` the
    moment the charge completed at 13:17:52, while its sibling correctly
    reported "Not charging".
    """
    charging = _finish_sensor(4)
    assert charging.available is True
    assert charging._get_sensor_value() is not None

    for state, label in ((1, "Standby"), (2, "Connected"), (5, "Charge Complete")):
        idle = _finish_sensor(state)
        assert idle.available is False, (
            f"in {label} there is no finish time to state, so the entity must be "
            "unavailable rather than publish a blank HA records as `unknown`"
        )


def test_charging_finish_time_is_unavailable_when_the_soc_inputs_are_missing() -> None:
    """No Target SOC is the same kind of blank, and gets the same answer."""
    assert _finish_sensor(4, helpers=None).available is False


def test_charging_finish_time_survives_a_missed_poll_while_charging() -> None:
    """Availability must not flap faster than the value it guards.

    The reading is held through the grace window, so the entity has to stay
    available for that window too — otherwise a single missed poll writes an
    `unavailable` row on the way down and another on the way back up, which is
    the churn the hold exists to prevent.
    """
    sensor = _finish_sensor(4)
    sensor._attr_native_value = datetime(2026, 6, 18, 10, 30)

    # Simulate the charger missing a poll: still inside the grace window.
    sensor._updater.available = False
    object.__setattr__(sensor, "_grace_started", None)
    if hasattr(type(sensor), "_in_availability_grace"):
        import unittest.mock as _m
        with _m.patch.object(
            type(sensor), "_in_availability_grace", property(lambda self: True)
        ):
            assert sensor.available is True, "a held reading must stay visible"
