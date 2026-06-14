from custom_components.eveus.number import SCHEDULE_LIMIT_NUMBERS

K = {d.key: d for d in SCHEDULE_LIMIT_NUMBERS}


def test_schedule_energy_is_one_to_one_not_thousand():
    d = K["schedule_1_energy_limit"]
    assert d.command == "sh1EnergyValue"
    assert d.device_to_ha == 1.0 and d.ha_to_device == 1.0  # contrast with global energy


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
