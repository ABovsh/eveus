import logging

from custom_components.eveus import switch as switch_mod
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


# ─── full description-field contract for every switch (name/icon/extra) ─────

_EXPECTED = {
    "stop_charging": ("Stop Charging", "mdi:ev-station", ()),
    "one_charge": ("One Charge", "mdi:lightning-bolt", ()),
    "schedule_1_enabled": ("Schedule 1 Enabled", "mdi:calendar-clock", ()),
    "schedule_2_enabled": ("Schedule 2 Enabled", "mdi:calendar-clock", ()),
    "ground_protection": ("Ground Protection", "mdi:shield-check", ()),
    "ocpp": ("Connect to OCPP", "mdi:cloud-sync", ("ocppVendor",)),
    "limit_disable_all": ("Limit: disable all", "mdi:cancel", ()),
    "limit_time_enabled": ("Limit: Time enabled", "mdi:timer-sand", ()),
    "limit_energy_enabled": ("Limit: Energy enabled", "mdi:lightning-bolt", ()),
    "limit_cost_enabled": ("Limit: Cost enabled", "mdi:cash", ()),
    "schedule_1_current_limit_enabled": (
        "Schedule 1 Current limit enabled", "mdi:current-ac", (),
    ),
    "schedule_1_energy_limit_enabled": (
        "Schedule 1 Energy limit enabled", "mdi:lightning-bolt", (),
    ),
    "schedule_2_current_limit_enabled": (
        "Schedule 2 Current limit enabled", "mdi:current-ac", (),
    ),
    "schedule_2_energy_limit_enabled": (
        "Schedule 2 Energy limit enabled", "mdi:lightning-bolt", (),
    ),
}


def test_every_switch_description_matches_documented_contract():
    assert set(KEYS) == set(_EXPECTED)
    for key, (name, icon, extra) in _EXPECTED.items():
        d = KEYS[key]
        assert d.name == name, key
        assert d.icon == icon, key
        assert d.command_extra == extra, key


def test_ocpp_switch_has_matching_command_and_state_key():
    d = KEYS["ocpp"]
    assert d.command == "ocppEnabled"
    assert d.state_key == "ocppEnabled"


def test_module_logger_is_a_real_logger():
    assert isinstance(switch_mod._LOGGER, logging.Logger)


def test_icon_energy_constant_value():
    assert switch_mod._ICON_ENERGY == "mdi:lightning-bolt"
