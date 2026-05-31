"""SOC-mode constants and helper."""
from types import SimpleNamespace

from custom_components.eveus import const


def test_mode_constants_exist():
    assert const.SOC_MODE_BASIC == "basic"
    assert const.SOC_MODE_ADVANCED == "advanced"
    assert const.SOC_MODE_OPTIONS == [const.SOC_MODE_BASIC, const.SOC_MODE_ADVANCED]


def test_seed_conf_keys_and_defaults():
    assert const.CONF_SOC_MODE == "soc_mode"
    assert const.CONF_INITIAL_SOC == "initial_soc"
    assert const.CONF_TARGET_SOC == "target_soc"
    assert const.CONF_BATTERY_CAPACITY == "battery_capacity"
    assert const.CONF_SOC_CORRECTION == "soc_correction"
    assert const.DEFAULT_INITIAL_SOC == 20
    assert const.DEFAULT_TARGET_SOC == 80
    assert const.DEFAULT_BATTERY_CAPACITY == 50
    assert const.SOC_CORRECTION_MAX == 20
    assert const.SOC_INPUT_LIMITS["battery_capacity"] == (10, 160)
    assert const.SOC_INPUT_LIMITS["soc_correction"] == (0, 20)
    assert const.SOC_INPUT_LIMITS["initial_soc"] == (0, 100)
    assert const.SOC_INPUT_LIMITS["target_soc"] == (0, 100)


def test_dispatcher_signal_is_entry_scoped():
    assert const.soc_update_signal("abc") == "eveus_soc_update_abc"
    assert const.soc_update_signal("abc") != const.soc_update_signal("xyz")


def test_get_soc_mode_reads_entry_data():
    entry = SimpleNamespace(data={const.CONF_SOC_MODE: "basic"})
    assert const.get_soc_mode(entry) == const.SOC_MODE_BASIC
    assert const.get_soc_mode(SimpleNamespace(data={})) == const.SOC_MODE_ADVANCED
