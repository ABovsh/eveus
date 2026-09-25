"""price_energy_over_tariffs: remaining energy priced per charger tariff window."""

import pytest

from custom_components.eveus.utils import price_energy_over_tariffs

RATES = (4.0, 2.0, 1.0)
NIGHT = {1: (1380, 420)}  # rate 2, 23:00-07:00


def test_no_windows_prices_everything_at_the_primary_rate():
    assert price_energy_over_tariffs(10, 6000, 600, 0, RATES, {}) == pytest.approx(40)


def test_a_window_starts_at_its_start_minute_and_ends_before_its_stop_minute():
    # 60 kWh/h: one minute = 1 kWh.
    assert price_energy_over_tariffs(1, 60000, 1380, 1, RATES, NIGHT) == pytest.approx(2)
    assert price_energy_over_tariffs(1, 60000, 420, 0, RATES, NIGHT) == pytest.approx(4)
    assert price_energy_over_tariffs(2, 60000, 1379, 0, RATES, NIGHT) == pytest.approx(4 + 2)
    assert price_energy_over_tariffs(2, 60000, 419, 1, RATES, NIGHT) == pytest.approx(2 + 4)


def test_a_rate_3_window_uses_rate_3():
    assert price_energy_over_tariffs(2, 60000, 599, 0, RATES, {2: (600, 660)}) == pytest.approx(4 + 1)


def test_a_charge_longer_than_a_day_crosses_the_window_again():
    # 1 kWh per minute for 1440 + 60 minutes from 07:00: 16 h day, 8 h night, 1 h day.
    cost = price_energy_over_tariffs(1500, 60000, 420, 0, RATES, NIGHT)
    assert cost == pytest.approx(960 * 4 + 480 * 2 + 60 * 4)


def test_disjoint_rate_2_and_3_windows_are_both_priced():
    windows = {1: (0, 60), 2: (60, 120)}
    assert price_energy_over_tariffs(180, 60000, 0, 1, RATES, windows) == pytest.approx(60 * 2 + 60 * 1 + 60 * 4)


@pytest.mark.parametrize(
    ("power", "minute", "active", "windows"),
    [
        (0, 600, 0, NIGHT),                       # no power
        (6000, 600, 1, NIGHT),                    # charger disagrees with the clock
        (6000, 600, 0, {1: (600, 600)}),          # zero-length window
        (6000, 600, 0, {1: (0, 120), 2: (60, 180)}),   # overlap
        (6000, 600, 0, {1: (1380, 420), 2: (0, 60)}),  # overlap across midnight
    ],
)
def test_untrusted_models_return_none(power, minute, active, windows):
    assert price_energy_over_tariffs(10, power, minute, active, RATES, windows) is None
