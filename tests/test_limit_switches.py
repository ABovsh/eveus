import logging
from types import SimpleNamespace

import pytest
from homeassistant.const import EntityCategory

from conftest import EveusTestUpdater
from custom_components.eveus import switch as switch_mod
from custom_components.eveus.const import CONF_FIRMWARE_FAMILY
from custom_components.eveus.reve import FAMILY_CLASSIC, FAMILY_REVE
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
    "household_socket_16_a": ("Household Socket 16 A", "mdi:power-socket-eu", ()),
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


class _SocketUpdater(EveusTestUpdater):
    def __init__(self, data):
        super().__init__(data)
        self.listeners = []

    def async_add_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)


async def _setup_socket_switch(updater, family):
    unload = []
    entry = SimpleNamespace(
        data={CONF_FIRMWARE_FAMILY: family, "soc_mode": "basic"},
        runtime_data=SimpleNamespace(updater=updater, device_number=2),
        async_on_unload=unload.append,
    )
    added = []
    await switch_mod.async_setup_entry(None, entry, added.extend)
    return added, unload


def _socket_switches(entities):
    return [entity for entity in entities if entity.name == "Household Socket 16 A"]


@pytest.mark.parametrize(("raw", "expected"), [(0, False), ("1", True), (2, None)])
async def test_household_socket_switch_reads_and_writes_the_reported_flag(raw, expected):
    updater = _SocketUpdater({"restricted_mode": raw})
    added, unload = await _setup_socket_switch(updater, FAMILY_CLASSIC)
    (entity,) = _socket_switches(added)
    assert not unload
    assert entity.unique_id == "eveus2_household_socket_16_a"
    assert entity.entity_category is EntityCategory.CONFIG
    assert entity.extra_state_attributes is None
    assert entity.assumed_state is False
    assert entity.extra_restore_state_data is None

    writes = []
    entity.async_write_ha_state = lambda: writes.append(entity.is_on)
    entity._handle_coordinator_update()
    entity._handle_coordinator_update()
    assert writes == [expected]

    await entity.async_turn_on()
    assert entity.is_on is True
    await entity.async_turn_off()
    assert entity.is_on is False
    assert updater.commands == [("restricted_mode", 1), ("restricted_mode", 0)]
    assert updater.command_extras == [None, None]


async def test_household_socket_switch_is_added_once_when_the_key_first_appears():
    updater = _SocketUpdater({})
    added, unload = await _setup_socket_switch(updater, FAMILY_CLASSIC)
    assert _socket_switches(added) == []
    (on_poll,) = updater.listeners
    on_poll()
    assert _socket_switches(added) == []

    updater.data = {"restricted_mode": 0}
    on_poll()
    on_poll()
    (entity,) = _socket_switches(added)
    assert entity.unique_id == "eveus2_household_socket_16_a"
    (unsubscribe,) = unload
    unsubscribe()
    assert not updater.listeners


async def test_reve_skips_the_household_socket_switch():
    updater = _SocketUpdater({})
    added, unload = await _setup_socket_switch(updater, FAMILY_REVE)
    assert _socket_switches(added) == []
    assert not updater.listeners
    assert not unload
