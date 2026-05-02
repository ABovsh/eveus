"""Unit tests for Eveus switch and number control behavior."""
from __future__ import annotations

import asyncio
import time

from homeassistant.core import State

from custom_components.eveus.number import EveusCurrentNumber
from custom_components.eveus.switch import (
    BaseSwitchEntity,
    EveusResetCounterASwitch,
    SWITCH_DESCRIPTIONS,
)


class _Updater:
    host = "192.168.1.50"
    available = True
    last_update_success = True

    def __init__(self, data: dict[str, object] | None = None) -> None:
        self.data = data or {}
        self.commands: list[tuple[str, object]] = []
        self.command_result = True

    def async_add_listener(self, *args: object, **kwargs: object):
        return lambda: None

    async def send_command(self, command: str, value: object) -> bool:
        self.commands.append((command, value))
        return self.command_result


def _disable_state_writes(entity: object) -> None:
    entity.async_write_ha_state = lambda: None


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

    assert entity.native_value == 14.0
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

    assert entity.is_on is False

    entity._pending_command = True
    assert entity.is_on is False
    assert entity._resolve_state() is False

    entity._pending_command = None
    entity._optimistic_state = True
    entity._optimistic_state_time = time.time()
    assert entity.is_on is False
    assert entity._resolve_state() is True

    entity._optimistic_state_time = 0
    updater.data = {"oneCharge": "1"}
    assert entity.is_on is False
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

    with pytest.raises(HomeAssistantError):
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


def test_reset_counter_switch_status_and_reset_behavior_unchanged() -> None:
    updater = _Updater({"IEM1": "5.5"})
    entity = EveusResetCounterASwitch(updater)
    _disable_state_writes(entity)

    assert entity.is_on is False
    entity._safe_mode = False
    assert entity.is_on is False
    entity._handle_coordinator_update()
    assert entity.is_on is True

    asyncio.run(entity.async_turn_on())
    assert updater.commands == []

    asyncio.run(entity.async_turn_off())
    assert updater.commands == [("rstEM1", 0)]

    updater.command_result = False
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises(HomeAssistantError):
        asyncio.run(entity.async_turn_off())
    assert updater.commands == [("rstEM1", 0), ("rstEM1", 0)]


def test_stop_charging_switch_raises_on_command_failure() -> None:
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    updater = _Updater({"evseEnabled": "0"})
    updater.command_result = False
    entity = _stop_charging_switch(updater)
    _disable_state_writes(entity)

    with pytest.raises(HomeAssistantError):
        asyncio.run(entity.async_turn_on())


def test_current_number_raises_on_command_failure() -> None:
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    updater = _Updater({"currentSet": "16"})
    updater.command_result = False
    entity = EveusCurrentNumber(updater, "32A")
    _disable_state_writes(entity)

    with pytest.raises(HomeAssistantError):
        asyncio.run(entity.async_set_native_value(20))


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
