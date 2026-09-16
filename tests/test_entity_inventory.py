"""Freeze the exact entity identity the integration constructs.

P4 collapses duplicated lifecycles/trackers/getters across the platform
modules. None of that refactor may rename an entity, change its platform, or
touch its unique_id — HA keys the entity registry, history and every
automation/dashboard reference on `unique_id`. This test builds the entity
list the same way `__init__.async_setup_entry` does (each platform's
`async_setup_entry` reading `entry.runtime_data`), not from the entity
registry, so it catches a construction-path regression before HA ever sees it.

Config is deliberately at "everything present": a model set and the default
(advanced) SOC mode, which is the config that yields the full 85-entity set
measured in `docs/internal/plan-2026-09-16-consolidation.md`.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from conftest import EveusTestUpdater, TEST_HOST, TEST_PASSWORD, TEST_USERNAME
from custom_components.eveus import EveusRuntimeData
from custom_components.eveus.binary_sensor import (
    async_setup_entry as setup_binary_sensor,
)
from custom_components.eveus.button import async_setup_entry as setup_button
from custom_components.eveus.const import CONF_MODEL, MODEL_16A
from custom_components.eveus.ev_sensors import CachedSOCCalculator
from custom_components.eveus.number import async_setup_entry as setup_number
from custom_components.eveus.select import async_setup_entry as setup_select
from custom_components.eveus.sensor import async_setup_entry as setup_sensor
from custom_components.eveus.soc_limit import SocLimitController
from custom_components.eveus.switch import async_setup_entry as setup_switch
from custom_components.eveus.time import async_setup_entry as setup_time

# platform -> (setup function, frozen sorted unique_id list)
_EXPECTED: dict[str, tuple[object, tuple[str, ...]]] = {
    "sensor": (
        setup_sensor,
        (
            "eveus_active_rate_cost",
            "eveus_adaptive_charging",
            "eveus_adaptive_current_limit",
            "eveus_battery_voltage",
            "eveus_box_temperature",
            "eveus_charging_finish_time",
            "eveus_connection_quality",
            "eveus_cost_to_target_soc",
            "eveus_counter_a_cost",
            "eveus_counter_a_energy",
            "eveus_counter_b_cost",
            "eveus_counter_b_energy",
            "eveus_current",
            "eveus_current_set",
            "eveus_energy_to_target_soc",
            "eveus_ground",
            "eveus_last_session_cost",
            "eveus_last_session_duration",
            "eveus_last_session_energy",
            "eveus_leakage_current",
            "eveus_leakage_current_peak",
            "eveus_not_charging_reason",
            "eveus_plug_temperature",
            "eveus_power",
            "eveus_primary_rate_cost",
            "eveus_rate_2_cost",
            "eveus_rate_2_status",
            "eveus_rate_3_cost",
            "eveus_rate_3_status",
            "eveus_schedule_1",
            "eveus_schedule_2",
            "eveus_session_cost",
            "eveus_session_energy",
            "eveus_session_time",
            "eveus_soc_energy",
            "eveus_soc_percent",
            "eveus_state",
            "eveus_substate",
            "eveus_time_drift",
            "eveus_time_to_target_soc",
            "eveus_total_energy",
            "eveus_voltage",
            "eveus_wifi_signal",
        ),
    ),
    "binary_sensor": (
        setup_binary_sensor,
        ("eveus_car_connected", "eveus_ocpp_connected", "eveus_session_active"),
    ),
    "switch": (
        setup_switch,
        (
            "eveus_connect_to_ocpp",
            "eveus_ground_protection",
            "eveus_limit_cost_enabled",
            "eveus_limit_disable_all",
            "eveus_limit_energy_enabled",
            "eveus_limit_soc_enabled",
            "eveus_limit_time_enabled",
            "eveus_one_charge",
            "eveus_schedule_1_current_limit_enabled",
            "eveus_schedule_1_enabled",
            "eveus_schedule_1_energy_limit_enabled",
            "eveus_schedule_2_current_limit_enabled",
            "eveus_schedule_2_enabled",
            "eveus_schedule_2_energy_limit_enabled",
            "eveus_stop_charging",
        ),
    ),
    "number": (
        setup_number,
        (
            "eveus_battery_capacity",
            "eveus_charging_current",
            "eveus_initial_soc",
            "eveus_limit_cost",
            "eveus_limit_energy",
            "eveus_limit_time",
            "eveus_schedule_1_current_limit",
            "eveus_schedule_1_energy_limit",
            "eveus_schedule_2_current_limit",
            "eveus_schedule_2_energy_limit",
            "eveus_soc_correction",
            "eveus_target_soc",
            "eveus_undervoltage_threshold",
        ),
    ),
    "button": (
        setup_button,
        (
            "eveus_force_refresh",
            "eveus_reset_counter_a",
            "eveus_reset_counter_b",
            "eveus_sync_time",
        ),
    ),
    "select": (
        setup_select,
        ("eveus_adaptive_mode", "eveus_minimum_voltage", "eveus_time_zone"),
    ),
    "time": (
        setup_time,
        (
            "eveus_schedule_1_start",
            "eveus_schedule_1_stop",
            "eveus_schedule_2_start",
            "eveus_schedule_2_stop",
        ),
    ),
}

_TOTAL_ENTITIES = 85


def _entry() -> SimpleNamespace:
    updater = EveusTestUpdater(model=MODEL_16A)
    soc_calculator = CachedSOCCalculator()
    soc_limit = SocLimitController(None, updater, soc_calculator)
    runtime_data = EveusRuntimeData(
        updater=updater,
        device_number=1,
        title="Eveus Charger",
        soc_calculator=soc_calculator,
        soc_limit=soc_limit,
        phases=1,
    )
    return SimpleNamespace(
        entry_id="entry-id",
        data={
            CONF_HOST: TEST_HOST,
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: TEST_PASSWORD,
            CONF_MODEL: MODEL_16A,
        },
        runtime_data=runtime_data,
    )


async def _build_inventory() -> dict[str, list[str]]:
    entry = _entry()
    inventory: dict[str, list[str]] = {}
    for platform, (setup_fn, _expected) in _EXPECTED.items():
        collected: list[object] = []

        def _add(entities, **_kwargs) -> None:
            collected.extend(entities)

        await setup_fn(None, entry, _add)
        inventory[platform] = sorted(entity.unique_id for entity in collected)
    return inventory


def test_entity_inventory_is_frozen() -> None:
    """The setup path must build exactly these unique_ids, per platform."""
    inventory = asyncio.run(_build_inventory())
    for platform, (_setup_fn, expected) in _EXPECTED.items():
        assert inventory[platform] == sorted(expected), (
            f"{platform} entity inventory drifted"
        )


def test_entity_inventory_totals_85() -> None:
    inventory = asyncio.run(_build_inventory())
    total = sum(len(ids) for ids in inventory.values())
    assert total == _TOTAL_ENTITIES

    all_ids = [uid for ids in inventory.values() for uid in ids]
    assert len(all_ids) == len(set(all_ids)), "duplicate unique_id across platforms"
