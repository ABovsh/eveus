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


def _set_up_numbers(soc_mode: str = "advanced") -> tuple[dict, CachedSOCCalculator]:
    calculator = CachedSOCCalculator()
    entry = SimpleNamespace(
        entry_id="entry-id",
        data={"model": "16A", "soc_mode": soc_mode},
        runtime_data=SimpleNamespace(
            updater=EveusTestUpdater({}, model="16A"),
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
