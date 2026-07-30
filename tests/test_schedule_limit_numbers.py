from homeassistant.components.number import NumberMode

from custom_components.eveus.const import MIN_CURRENT
from custom_components.eveus.number import SCHEDULE_LIMIT_NUMBERS

K = {d.key: d for d in SCHEDULE_LIMIT_NUMBERS}


def test_schedule_energy_is_one_to_one_not_thousand():
    d = K["schedule_1_energy_limit"]
    assert d.command == "sh1EnergyValue"
    assert d.device_to_ha == 1.0
    assert d.ha_to_device == 1.0  # contrast with global energy


def test_schedule_current_targets_value_field():
    d = K["schedule_2_current_limit"]
    assert d.command == "sh2CurrentValue"
    assert d.native_unit_of_measurement == "A"


def test_all_four_present():
    assert set(K) == {
        "schedule_1_current_limit",
        "schedule_1_energy_limit",
        "schedule_2_current_limit",
        "schedule_2_energy_limit",
    }


def test_schedule_current_description_full_contract():
    for n in (1, 2):
        d = K[f"schedule_{n}_current_limit"]
        assert d.icon == "mdi:current-ac"
        assert d.state_key == f"sh{n}CurrentValue"
        assert d.native_min_value == MIN_CURRENT
        assert d.native_max_value == 32
        assert d.read_min_value == 0.0
        assert d.native_step == 1
        assert d.native_unit_of_measurement == "A"
        assert d.mode == NumberMode.BOX


def test_schedule_energy_description_full_contract():
    for n in (1, 2):
        d = K[f"schedule_{n}_energy_limit"]
        assert d.icon == "mdi:lightning-bolt"
        assert d.state_key == f"sh{n}EnergyValue"
        assert d.native_min_value == 0
        assert d.native_max_value == 100
        assert d.native_step == 1
        assert d.native_unit_of_measurement == "kWh"
        assert d.display_precision == 3
        assert d.mode == NumberMode.BOX
