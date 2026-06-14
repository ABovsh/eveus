from custom_components.eveus.switch import SWITCH_DESCRIPTIONS

KEYS = {d.key: d for d in SWITCH_DESCRIPTIONS}

def test_all_new_limit_switches_present():
    for key, field in (
        ("limit_time_enabled", "timeLimitS"),
        ("limit_energy_enabled", "energyLimitS"),
        ("limit_cost_enabled", "moneyLimitS"),
        ("limit_disable_all", "suspendLimits"),
        ("schedule_1_current_limit_enabled", "sh1CurrentEnable"),
        ("schedule_1_energy_limit_enabled", "sh1EnergyEnable"),
        ("schedule_2_current_limit_enabled", "sh2CurrentEnable"),
        ("schedule_2_energy_limit_enabled", "sh2EnergyEnable"),
    ):
        assert key in KEYS, f"missing switch {key}"
        assert KEYS[key].command == field
        assert KEYS[key].state_key == field
