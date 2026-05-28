"""Unit tests for Eveus switch and number control behavior."""
from __future__ import annotations

import asyncio
import time

import pytest
from homeassistant.core import State

from conftest import EveusTestUpdater as _Updater
from conftest import disable_state_writes as _disable_state_writes
from custom_components.eveus.number import EveusCurrentNumber
from custom_components.eveus.number import async_setup_entry as async_setup_number_entry
from custom_components.eveus.button import (
    EveusResetCounterAButton,
    EveusResetCounterBButton,
)
from custom_components.eveus.switch import (
    BaseSwitchEntity,
    SWITCH_DESCRIPTIONS,
    async_setup_entry as async_setup_switch_entry,
)


def _one_charge_switch(updater: _Updater) -> BaseSwitchEntity:
    return BaseSwitchEntity(updater, SWITCH_DESCRIPTIONS[1])


def _stop_charging_switch(updater: _Updater) -> BaseSwitchEntity:
    return BaseSwitchEntity(updater, SWITCH_DESCRIPTIONS[0])


def test_current_number_native_value_precedence_and_restore() -> None:
    updater = _Updater({"currentSet": "16"})
    entity = EveusCurrentNumber(updater, "32A")

    assert entity.native_value == 16

    entity._pending_value = 20
    assert entity.native_value == 16
    assert entity._resolve_value() == 16

    entity._pending_value = None
    entity._optimistic_value = 24
    entity._optimistic_value_time = time.time()
    assert entity.native_value == 16
    assert entity._resolve_value() == 24

    entity._optimistic_value_time = 0
    updater.data = {}
    entity._last_device_value = 18
    entity._last_successful_read = time.time()
    assert entity.native_value == 16
    assert entity._resolve_value() == 18

    asyncio.run(entity._async_restore_state(State("number.current", "19")))
    assert entity._last_device_value == 19


def test_current_number_set_value_clamps_and_records_command() -> None:
    updater = _Updater({"currentSet": "16"})
    entity = EveusCurrentNumber(updater, "16A")
    _disable_state_writes(entity)

    asyncio.run(entity.async_set_native_value(99))

    assert updater.commands == [("currentSet", 16)]
    assert entity._optimistic_value == 16


def test_current_number_update_reconciles_optimistic_value() -> None:
    updater = _Updater({"currentSet": "12"})
    entity = EveusCurrentNumber(updater, "16A")
    _disable_state_writes(entity)
    entity._optimistic_value = 12
    entity._optimistic_value_time = time.time()

    entity._handle_coordinator_update()

    assert entity._optimistic_value is None
    assert entity._last_device_value == 12


def test_current_number_update_clears_stale_mismatched_optimistic_value() -> None:
    updater = _Updater({"currentSet": "10"})
    entity = EveusCurrentNumber(updater, "16A")
    _disable_state_writes(entity)
    entity._optimistic_value = 14
    entity._optimistic_value_time = 0

    entity._handle_coordinator_update()

    assert entity._optimistic_value is None
    assert entity._last_device_value == 10


def test_current_number_ignores_stale_coordinator_update_while_command_pending() -> None:
    updater = _Updater({"currentSet": "10"})
    entity = EveusCurrentNumber(updater, "16A")
    _disable_state_writes(entity)
    entity._pending_value = 14.0
    entity._attr_native_value = 14.0

    entity._handle_coordinator_update()

    assert entity.native_value == pytest.approx(14.0)
    assert entity._last_device_value is None


def test_current_number_handles_failed_command() -> None:
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    updater = _Updater({"currentSet": "16"})
    updater.command_result = False
    entity = EveusCurrentNumber(updater, "16A")
    _disable_state_writes(entity)

    with pytest.raises(HomeAssistantError):
        asyncio.run(entity.async_set_native_value(12))

    assert updater.commands == [("currentSet", 12)]
    assert entity._optimistic_value is None


def test_current_number_wraps_unexpected_command_exception() -> None:
    from homeassistant.exceptions import HomeAssistantError

    class BrokenUpdater(_Updater):
        async def send_command(self, command: str, value: object, *, retry: bool = True) -> bool:
            raise RuntimeError("network disappeared")

    entity = EveusCurrentNumber(BrokenUpdater({"currentSet": "16"}), "16A")
    _disable_state_writes(entity)

    with pytest.raises(HomeAssistantError, match="Failed to set charging current"):
        asyncio.run(entity.async_set_native_value(12))

    assert entity._pending_value is None
    assert entity._optimistic_value is None


def test_current_number_restore_ignores_invalid_or_out_of_range_values() -> None:
    entity = EveusCurrentNumber(_Updater({"currentSet": "16"}), "16A")

    asyncio.run(entity._async_restore_state(State("number.current", "bad")))
    assert entity._last_device_value is None

    asyncio.run(entity._async_restore_state(State("number.current", "99")))
    assert entity._last_device_value is None


def test_current_number_returns_none_for_stale_device_value() -> None:
    entity = EveusCurrentNumber(_Updater({}), "16A")
    entity._last_device_value = 12
    entity._last_successful_read = 0

    assert entity.native_value is None


def test_switch_state_precedence_restore_and_commands() -> None:
    updater = _Updater({"oneCharge": "0"})
    entity = _one_charge_switch(updater)
    _disable_state_writes(entity)

    # Cached is_on starts unknown (None) until a resolve/coordinator update.
    assert entity.is_on is None

    entity._pending_command = True
    assert entity.is_on is None
    assert entity._resolve_state() is False

    entity._pending_command = None
    entity._optimistic_state = True
    entity._optimistic_state_time = time.time()
    assert entity.is_on is None
    assert entity._resolve_state() is True

    entity._optimistic_state_time = 0
    updater.data = {"oneCharge": "1"}
    assert entity.is_on is None
    assert entity._resolve_state() is True

    asyncio.run(entity._async_restore_state(State("switch.one", "off")))
    assert entity._last_device_state is False

    asyncio.run(entity.async_turn_on())
    asyncio.run(entity.async_turn_off())
    assert updater.commands[-2:] == [("oneCharge", 1), ("oneCharge", 0)]


def test_switch_update_reconciles_optimistic_state() -> None:
    updater = _Updater({"oneCharge": "1"})
    entity = _one_charge_switch(updater)
    _disable_state_writes(entity)
    entity._optimistic_state = True
    entity._optimistic_state_time = time.time()

    entity._handle_coordinator_update()

    assert entity._optimistic_state is None
    assert entity._last_device_state is True


def test_switch_update_clears_stale_mismatched_optimistic_state() -> None:
    updater = _Updater({"oneCharge": "0"})
    entity = _one_charge_switch(updater)
    _disable_state_writes(entity)
    entity._optimistic_state = True
    entity._optimistic_state_time = 0

    entity._handle_coordinator_update()

    assert entity._optimistic_state is None
    assert entity._last_device_state is False


def test_switch_ignores_stale_coordinator_update_while_command_pending() -> None:
    updater = _Updater({"oneCharge": "0"})
    entity = _one_charge_switch(updater)
    _disable_state_writes(entity)
    entity._pending_command = True
    entity._attr_is_on = True

    entity._handle_coordinator_update()

    assert entity.is_on is True
    assert entity._last_device_state is None


def test_switch_failed_command_does_not_set_optimistic_state() -> None:
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    updater = _Updater({"oneCharge": "0"})
    updater.command_result = False
    entity = _one_charge_switch(updater)
    _disable_state_writes(entity)

    with pytest.raises(HomeAssistantError, match="did not accept"):
        asyncio.run(entity.async_turn_on())

    assert updater.commands == [("oneCharge", 1)]
    assert entity._optimistic_state is None


def test_stop_charging_switch_preserves_existing_semantics() -> None:
    updater = _Updater({"evseEnabled": "0"})
    entity = _stop_charging_switch(updater)
    _disable_state_writes(entity)

    asyncio.run(entity.async_turn_on())
    asyncio.run(entity.async_turn_off())

    assert updater.commands == [("evseEnabled", 1), ("evseEnabled", 0)]


def test_reset_counter_buttons_emit_reset_commands() -> None:
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    updater = _Updater({"IEM1": "5.5", "IEM2": "2.2"})
    button_a = EveusResetCounterAButton(updater)
    button_b = EveusResetCounterBButton(updater)
    _disable_state_writes(button_a)
    _disable_state_writes(button_b)

    asyncio.run(button_a.async_press())
    asyncio.run(button_b.async_press())
    assert updater.commands == [("rstEM1", 0), ("rstEM2", 0)]
    assert updater.last_retry is False

    updater.command_result = False
    with pytest.raises(HomeAssistantError):
        asyncio.run(button_a.async_press())
    with pytest.raises(HomeAssistantError):
        asyncio.run(button_b.async_press())


def test_stop_charging_switch_raises_on_command_failure() -> None:
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    updater = _Updater({"evseEnabled": "0"})
    updater.command_result = False
    entity = _stop_charging_switch(updater)
    _disable_state_writes(entity)

    with pytest.raises(HomeAssistantError, match="did not accept"):
        asyncio.run(entity.async_turn_on())
    assert updater.commands == [("evseEnabled", 1)]


def test_current_number_raises_on_command_failure() -> None:
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    updater = _Updater({"currentSet": "16"})
    updater.command_result = False
    entity = EveusCurrentNumber(updater, "32A")
    _disable_state_writes(entity)

    with pytest.raises(HomeAssistantError, match="did not accept"):
        asyncio.run(entity.async_set_native_value(20))
    assert updater.commands == [("currentSet", 20)]


def test_switch_optimistic_state_survives_until_device_confirms() -> None:
    """Toggle ON, ensure optimistic ON survives a coordinator read that
    still shows OFF (charger hasn't committed yet)."""
    updater = _Updater({"evseEnabled": "0"})
    entity = _stop_charging_switch(updater)
    _disable_state_writes(entity)

    asyncio.run(entity.async_turn_on())
    assert entity.is_on is True
    assert entity._optimistic_state is True

    # Coordinator returns stale OFF — optimistic must hold ON within TTL window.
    entity._handle_coordinator_update()
    assert entity.is_on is True

    # Device finally confirms ON — optimistic clears, state stays ON.
    updater.data = {"evseEnabled": "1"}
    entity._handle_coordinator_update()
    assert entity._optimistic_state is None
    assert entity.is_on is True


def test_switch_rapid_toggle_does_not_flicker_back() -> None:
    """ON, then OFF 2s later — a stale ON read must not flip the entity."""
    updater = _Updater({"evseEnabled": "0"})
    entity = _stop_charging_switch(updater)
    _disable_state_writes(entity)

    asyncio.run(entity.async_turn_on())
    asyncio.run(entity.async_turn_off())
    assert entity.is_on is False
    assert entity._optimistic_state is False

    # Stale read still shows ON — optimistic OFF wins inside TTL.
    updater.data = {"evseEnabled": "1"}
    entity._handle_coordinator_update()
    assert entity.is_on is False

    # Device commits OFF — optimistic clears.
    updater.data = {"evseEnabled": "0"}
    entity._handle_coordinator_update()
    assert entity.is_on is False
    assert entity._optimistic_state is None


def test_switch_test_alias_properties_round_trip() -> None:
    entity = _one_charge_switch(_Updater({}))

    entity._optimistic_state_time = 123.0
    entity._last_device_state = True

    assert entity._optimistic_state_time == 123.0
    assert entity._last_device_state is True


def test_switch_resolves_recent_restored_state_when_payload_missing() -> None:
    entity = _one_charge_switch(_Updater({}))
    entity._last_device_state = True
    entity._last_successful_read = time.time()

    assert entity._resolve_state() is True


def test_switch_added_to_hass_resolves_initial_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updater = _Updater({"oneCharge": "1"})
    entity = _one_charge_switch(updater)

    async def noop_added_to_hass(self):
        return None

    monkeypatch.setattr(
        "custom_components.eveus.common_base.BaseEveusEntity.async_added_to_hass",
        noop_added_to_hass,
    )

    asyncio.run(entity.async_added_to_hass())

    assert entity.is_on is True


def test_switch_restore_ignores_invalid_state() -> None:
    entity = _one_charge_switch(_Updater({}))

    asyncio.run(entity._async_restore_state(State("switch.one", "unknown")))

    assert entity._last_device_state is None
    assert entity.is_on is None


def test_switch_setup_entry_adds_all_switches() -> None:
    added = []
    entry = type(
        "Entry",
        (),
        {
            "runtime_data": type(
                "RuntimeData",
                (),
                {"updater": _Updater({}), "device_number": 3},
            )()
        },
    )()

    asyncio.run(async_setup_switch_entry(None, entry, lambda entities: added.extend(entities)))

    assert [entity.entity_description.key for entity in added] == [
        description.key for description in SWITCH_DESCRIPTIONS
    ]
    assert all(entity.unique_id.startswith("eveus3_") for entity in added)


def test_number_setup_entry_skips_entity_when_model_is_missing() -> None:
    added = []
    entry = type(
        "Entry",
        (),
        {
            "data": {},
            "runtime_data": type(
                "RuntimeData",
                (),
                {"updater": _Updater({"currentSet": "16"}), "device_number": 1},
            )(),
        },
    )()

    asyncio.run(async_setup_number_entry(None, entry, lambda entities: added.extend(entities)))

    assert added == []
