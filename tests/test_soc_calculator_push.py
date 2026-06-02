"""CachedSOCCalculator pushed-value model."""
from custom_components.eveus.ev_sensors import CachedSOCCalculator
from custom_components.eveus.const import DEFAULT_SOC_CORRECTION


def test_starts_unavailable():
    calc = CachedSOCCalculator()
    assert calc.are_helpers_available() is False
    assert calc.battery_capacity is None
    assert calc.target_soc is None


def test_set_value_makes_soc_available():
    calc = CachedSOCCalculator()
    calc.set_value("initial_soc", 20)
    calc.set_value("battery_capacity", 50)
    calc.set_value("soc_correction", 7.5)
    assert calc.are_helpers_available() is True
    kwh = calc.get_soc_kwh(5.0)
    assert kwh is not None and kwh > 10


def test_target_soc_optional_for_availability():
    calc = CachedSOCCalculator()
    for k, v in (("initial_soc", 20), ("battery_capacity", 50), ("soc_correction", 7.5)):
        calc.set_value(k, v)
    assert calc.are_helpers_available() is True
    assert calc.target_soc is None
    calc.set_value("target_soc", 80)
    assert calc.target_soc == 80


def test_effective_correction_default_when_unset():
    calc = CachedSOCCalculator()
    assert calc.soc_correction == DEFAULT_SOC_CORRECTION
