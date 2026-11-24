"""Number entities as the platform setup hands them to Home Assistant.

Entities come from ``async_setup_entry`` (never built from a private helper),
and every assertion is on something HA or the user sees: entity category, the
error a rejected service call raises, the state writes, the SOC-update signal
the SOC sensors listen on, and the calculator the entry's SOC sensors share.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError

from conftest import EveusTestUpdater, HelperHass
from custom_components.eveus import number as number_module
from custom_components.eveus.ev_sensors import CachedSOCCalculator


def _set_up_numbers(
    soc_mode: str = "advanced",
    *,
    updater: EveusTestUpdater | None = None,
) -> tuple[dict, CachedSOCCalculator]:
    if updater is None:
        updater = EveusTestUpdater({}, model="16A")
    calculator = CachedSOCCalculator()
    entry = SimpleNamespace(
        entry_id="entry-id",
        data={"model": updater.model, "soc_mode": soc_mode},
        runtime_data=SimpleNamespace(
            updater=updater,
            device_number=1,
            soc_calculator=calculator,
        ),
    )
    added: list = []
    asyncio.run(number_module.async_setup_entry(object(), entry, added.extend))
    return {entity.name: entity for entity in added}, calculator


def _count_state_writes(entity) -> list[None]:
    writes: list[None] = []
    entity.async_write_ha_state = lambda: writes.append(None)
    return writes


@pytest.mark.parametrize(
    "name",
    [
        "Schedule 1 Current limit",
        "Schedule 1 Energy limit",
        "Schedule 2 Current limit",
        "Schedule 2 Energy limit",
    ],
)
def test_schedule_limits_are_listed_as_configuration(name: str) -> None:
    entities, _ = _set_up_numbers("basic")

    assert entities[name].entity_category is EntityCategory.CONFIG


@pytest.mark.parametrize("name", ["Charging Current", "Battery Capacity"])
def test_a_rejected_value_names_the_entity_in_the_error(name: str) -> None:
    entities, _ = _set_up_numbers()

    with pytest.raises(HomeAssistantError) as exc_info:
        asyncio.run(entities[name].async_set_native_value("abc"))

    assert str(exc_info.value).startswith(f"{name}: ")


def test_soc_inputs_share_the_entrys_calculator() -> None:
    entities, calculator = _set_up_numbers()
    capacity = entities["Battery Capacity"]
    _count_state_writes(capacity)

    asyncio.run(capacity.async_set_native_value(70))

    assert calculator.battery_capacity == 70


def test_every_soc_input_change_reaches_the_ha_state() -> None:
    entities, _ = _set_up_numbers()
    capacity = entities["Battery Capacity"]
    writes = _count_state_writes(capacity)

    asyncio.run(capacity.async_set_native_value(70))
    asyncio.run(capacity.async_set_native_value(80))
    asyncio.run(capacity.async_set_native_value(80))

    assert capacity.native_value == 80
    assert len(writes) == 2


def test_a_soc_input_change_signals_this_entrys_soc_sensors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entities, _ = _set_up_numbers()
    capacity = entities["Battery Capacity"]
    capacity.hass = HelperHass({})
    _count_state_writes(capacity)
    sent: list[tuple[object, str]] = []

    def _dispatcher_send(hass, signal, *args) -> None:
        sent.append((hass, signal, *args))

    monkeypatch.setattr(number_module, "async_dispatcher_send", _dispatcher_send)

    asyncio.run(capacity.async_set_native_value(70))

    assert sent == [(capacity.hass, "eveus_soc_update_entry-id")]


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("Charging Current", "currentSet"),
        ("Schedule 1 Current limit", "sh1CurrentValue"),
        ("Schedule 2 Current limit", "sh2CurrentValue"),
    ],
)
def test_current_bounds_follow_the_household_socket_mode(name, key):
    updater = EveusTestUpdater({key: 12, "restricted_mode": "1"}, model="32A")
    entities, _ = _set_up_numbers("basic", updater=updater)
    entity = entities[name]
    writes = []
    entity.async_write_ha_state = lambda: writes.append(
        (entity.native_value, entity.capability_attributes["max"])
    )
    assert entity.native_max_value == 16
    assert entity.native_value == 12
    entity._handle_coordinator_update()
    entity._handle_coordinator_update()
    assert writes == [(12, 16)]

    # Publish a bound-only change and a simultaneous value/bound change once each.
    updater.data = {key: 12, "restricted_mode": 0}
    entity._handle_coordinator_update()
    assert writes[-1] == (12, 32)
    updater.data = {key: 14, "restricted_mode": 1}
    entity._handle_coordinator_update()
    entity._handle_coordinator_update()
    assert writes == [(12, 16), (12, 32), (14, 16)]

    # Even if the latest poll has not reached the entity listener yet, clamp
    # against the current snapshot when sending a queued write.
    updater.data = {key: 14, "restricted_mode": 0}
    asyncio.run(entity.async_set_native_value(25))
    updater.data = {key: 25, "restricted_mode": 1}
    asyncio.run(entity.async_set_native_value(25))
    assert updater.commands == [(key, 25), (key, 16)]


@pytest.mark.parametrize("flag", [{}, {"restricted_mode": -1}, {"restricted_mode": 2}])
def test_missing_or_invalid_household_socket_flag_uses_the_normal_maximum(flag):
    updater = EveusTestUpdater(flag, model="40A")
    entities, _ = _set_up_numbers("basic", updater=updater)
    assert entities["Charging Current"].native_max_value == 40


def test_household_socket_mode_keeps_a_16a_models_bound_and_ignores_energy_limits():
    updater = EveusTestUpdater({"restricted_mode": 1}, model="16A")
    entities, _ = _set_up_numbers("basic", updater=updater)
    assert entities["Charging Current"].native_max_value == 16
    assert entities["Schedule 1 Current limit"].native_max_value == 16
    assert entities["Schedule 2 Current limit"].native_max_value == 16
    assert entities["Schedule 1 Energy limit"].native_max_value == 100
    assert entities["Schedule 2 Energy limit"].native_max_value == 100
