"""Cost to Target SOC before the charge starts: priced where the schedules put it.

Without a charge running, the estimate priced every kWh at the tariff in effect
now, so at 19:00 a car that Schedule 1 will charge 23:00-07:00 showed the day
rate (54.5 kWh x 4.32 = 235 on Anton's charger, 2026-10-18) for a charge billed
at the night rate. While a schedule is on, the charger draws only inside the
schedule windows, so the energy is placed there, at each schedule's current
(the charging current, or the schedule's current limit when lower), and priced
per tariff window like a running charge. With no schedule on, One Charge, REVE
manual mode or Stop, the charge would start at once (or not at all): the
estimate stays at the tariff in effect now.
"""
from __future__ import annotations

import pytest

from conftest import EveusTestUpdater, EV_HELPERS
from custom_components.eveus.ev_sensors import CachedSOCCalculator, CostToTargetSocSensor
from custom_components.eveus.utils import price_energy_in_schedules

RATES = (4.0, 2.0, 1.0)
NIGHT_RATE = {1: (1380, 420)}  # rate 2, 23:00-07:00
KW = 60000  # 1 kWh per minute


def test_a_night_schedule_is_priced_at_the_night_rate() -> None:
    # 19:00, 100 kWh: the 23:00-07:00 schedule takes it all at the night rate.
    assert price_energy_in_schedules(100, 1140, 0, RATES, NIGHT_RATE, ((1380, 420, KW),)) == pytest.approx(200)


def test_energy_beyond_one_window_waits_for_the_next() -> None:
    # 480 kWh fit the night window; 20 more go into the 09:00-18:00 one.
    schedules = ((1380, 420, KW), (540, 1080, KW))
    assert price_energy_in_schedules(500, 1140, 0, RATES, NIGHT_RATE, schedules) == pytest.approx(480 * 2 + 20 * 4)


def test_inside_a_window_the_charge_starts_now() -> None:
    # 16:00 inside 09:00-18:00: 120 kWh now at the day rate, then the night.
    schedules = ((1380, 420, KW), (540, 1080, KW))
    assert price_energy_in_schedules(200, 960, 0, RATES, NIGHT_RATE, schedules) == pytest.approx(120 * 4 + 80 * 2)


def test_a_window_across_a_tariff_edge_is_priced_on_both_sides() -> None:
    # 22:00-02:00 schedule: one hour before the night rate, three in it.
    assert price_energy_in_schedules(240, 1200, 0, RATES, NIGHT_RATE, ((1320, 120, KW),)) == pytest.approx(60 * 4 + 180 * 2)


def test_each_schedule_charges_at_its_own_power() -> None:
    # Half power in the night window: 240 kWh there, the rest at full power by day.
    schedules = ((1380, 420, KW / 2), (540, 1080, KW))
    assert price_energy_in_schedules(300, 1140, 0, RATES, NIGHT_RATE, schedules) == pytest.approx(240 * 2 + 60 * 4)


def test_a_charge_of_many_days_repeats_the_windows() -> None:
    # 480 kWh a night: three nights and a half.
    assert price_energy_in_schedules(1680, 1140, 0, RATES, NIGHT_RATE, ((1380, 420, KW),)) == pytest.approx(1680 * 2)


@pytest.mark.parametrize(
    ("active", "windows", "schedules"),
    [
        (0, NIGHT_RATE, ()),                                        # no schedule on
        (0, NIGHT_RATE, ((600, 600, KW),)),                         # zero-length schedule
        (0, NIGHT_RATE, ((1380, 420, 0),)),                         # no power
        (0, NIGHT_RATE, ((0, 120, KW), (60, 180, KW))),             # schedules overlap
        (1, NIGHT_RATE, ((1380, 420, KW),)),                        # charger disagrees with the clock
        (0, {1: (0, 120), 2: (60, 180)}, ((1380, 420, KW),)),       # rate windows overlap
        (0, {1: (600, 600)}, ((1380, 420, KW),)),                   # zero-length rate window
    ],
)
def test_untrusted_models_return_none(active, windows, schedules) -> None:
    assert price_energy_in_schedules(10, 1140, active, RATES, windows, schedules) is None


# ---------------------------------------------------------------------------
# The sensor
# ---------------------------------------------------------------------------

_DAY = 1790208000  # a charger-clock midnight
_ANTON = {
    # Anton's charger, 2026-10-18: 4.32 by day, 2.16 from 23:00 to 07:00.
    "timeZone": 3, "tarif": 432, "activeTarif": 0,
    "tarifAEnable": 1, "tarifAValue": 216, "tarifAStart": 1380, "tarifAStop": 420,
    "tarifBEnable": 1, "tarifBValue": 432, "tarifBStart": 420, "tarifBStop": 1380,
    "state": 2, "sessionEnergy": 0, "currentSet": 16, "voltMeas1": 230,
    "evseEnabled": 0, "oneCharge": 0,
    "sh1Enabled": 1, "sh1Start": 1380, "sh1Stop": 420, "sh1CurrentEnable": 1, "sh1CurrentValue": 16,
    "sh2Enabled": 0, "sh2Start": 540, "sh2Stop": 1080, "sh2CurrentEnable": 0, "sh2CurrentValue": 16,
}
# 20 % -> 80 % of 80 kWh with a 10 % loss.
_REMAINING = 48 / 0.9


def _cost(fields: dict, clock_hm: tuple[int, int] = (19, 0), *, phases: int = 1, manual: bool | None = None) -> float | None:
    calc = CachedSOCCalculator()
    for entity_id, value in EV_HELPERS.items():
        calc.set_value(entity_id.removeprefix("input_number.ev_"), float(value))
    updater = EveusTestUpdater({**fields, "systemTime": _DAY + clock_hm[0] * 3600 + clock_hm[1] * 60})
    updater.reve_manual_mode = manual
    if clock_hm[0] < 7 or clock_hm[0] >= 23:
        updater.data["activeTarif"] = 1
    elif "tarifBEnable" in fields and fields["tarifBEnable"]:
        updater.data["activeTarif"] = 2
    return CostToTargetSocSensor(updater, 1, calc, phases=phases)._get_sensor_value()


def test_at_19_00_a_night_schedule_is_priced_at_the_night_rate() -> None:
    assert _cost(_ANTON) == pytest.approx(_REMAINING * 2.16, abs=0.02)


def test_two_schedules_put_what_the_night_cannot_take_into_the_day() -> None:
    night_kwh = 16 * 230 / 1000 * 8
    fields = {**_ANTON, "sh2Enabled": 1}
    assert _cost(fields) == pytest.approx(night_kwh * 2.16 + (_REMAINING - night_kwh) * 4.32, abs=0.02)


def test_a_schedule_current_limit_slows_the_charge() -> None:
    # 10 A: 18.4 kWh the first night; Schedule 2 (no limit, 16 A) takes 33.12
    # by day, and the second night the last 1.81.
    night_kwh, day_kwh = 10 * 230 / 1000 * 8, 16 * 230 / 1000 * 9
    fields = {**_ANTON, "sh1CurrentValue": 10, "sh2Enabled": 1}
    expected = night_kwh * 2.16 + day_kwh * 4.32 + (_REMAINING - night_kwh - day_kwh) * 2.16
    assert _cost(fields) == pytest.approx(expected, abs=0.02)


def test_three_phases_charge_three_times_as_fast() -> None:
    fields = {**_ANTON, "sh2Enabled": 1}
    assert _cost(fields, phases=3) == pytest.approx(_REMAINING * 2.16, abs=0.02)


@pytest.mark.parametrize(
    ("override", "manual"),
    [
        ({"sh1Enabled": 0}, None),       # no schedule: the charge starts on plug-in
        ({"oneCharge": 1}, None),        # One Charge ignores the schedules
        ({}, True),                      # so does REVE manual mode
        ({"evseEnabled": 1}, None),      # Stop: no charge at all
    ],
)
def test_without_a_schedule_to_wait_for_the_rate_now_applies(override, manual) -> None:
    assert _cost({**_ANTON, **override}, manual=manual) == pytest.approx(_REMAINING * 4.32, abs=0.02)


def test_the_start_is_read_on_a_quarter_hour_grid(monkeypatch: pytest.MonkeyPatch) -> None:
    # Inside a window the estimate can shift as the window runs out; on a
    # 15-minute grid it can change at most four times an hour, not every poll.
    from custom_components.eveus import ev_sensors

    starts: list[float] = []
    monkeypatch.setattr(
        ev_sensors, "price_energy_in_schedules",
        lambda energy, minute, *rest: starts.append(minute) or 0.0,
    )
    for hm in ((16, 1), (16, 14), (16, 15), (16, 29)):
        _cost({**_ANTON, "sh2Enabled": 1}, hm)
    assert starts == [960, 960, 975, 975]


def test_a_schedule_of_one_watt_is_still_a_schedule() -> None:
    # Only a schedule without power is untrustworthy; 1 W is priced (slowly).
    assert price_energy_in_schedules(0.001, 1380, 1, RATES, NIGHT_RATE, ((1380, 420, 1),)) == pytest.approx(0.002)


def test_a_charge_starting_on_a_window_edge_is_priced() -> None:
    # 23:00 exactly: the night window opens now.
    assert price_energy_in_schedules(10, 1380, 1, RATES, NIGHT_RATE, ((1380, 420, KW),)) == pytest.approx(20)


def test_a_charge_after_the_last_edge_of_the_day_wraps_to_the_first() -> None:
    # 23:20, inside the night window, past the day's last edge (23:00).
    assert price_energy_in_schedules(100, 1400, 1, RATES, NIGHT_RATE, ((1380, 420, KW),)) == pytest.approx(200)
    # 23:20, schedule 09:00-18:00 only: waits across midnight for 09:00, day rate.
    assert price_energy_in_schedules(100, 1400, 1, RATES, NIGHT_RATE, ((540, 1080, KW),)) == pytest.approx(400)
