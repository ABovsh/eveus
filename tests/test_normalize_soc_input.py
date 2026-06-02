"""Unit tests for normalize_soc_input."""
from custom_components.eveus.utils import normalize_soc_input


def test_non_numeric_returns_default():
    assert normalize_soc_input("battery_capacity", "abc", 50) == 50
    assert normalize_soc_input("battery_capacity", None, 50) == 50


def test_non_finite_returns_default():
    assert normalize_soc_input("battery_capacity", float("nan"), 50) == 50
    assert normalize_soc_input("battery_capacity", float("inf"), 50) == 50


def test_out_of_range_clamps():
    assert normalize_soc_input("battery_capacity", 0, 50) == 10     # min 10
    assert normalize_soc_input("battery_capacity", 999, 50) == 160  # max 160
    assert normalize_soc_input("soc_correction", 25, 7.5) == 20     # max 20


def test_in_range_passthrough():
    assert normalize_soc_input("initial_soc", 65, 20) == 65
