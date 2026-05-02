"""Support for Eveus switches with optimistic UI and safety."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from . import EveusConfigEntry
from .common_base import BaseEveusEntity, ControlEntityMixin, OptimisticControlMixin
from .const import CONTROL_GRACE_PERIOD, OPTIMISTIC_CONTROL_TTL
from .utils import get_safe_value

_LOGGER = logging.getLogger(__name__)


class EveusSwitchEntityDescription(SwitchEntityDescription, frozen_or_thawed=True):
    """Description for Eveus switch entities."""

    command: str
    state_key: str


SWITCH_DESCRIPTIONS: tuple[EveusSwitchEntityDescription, ...] = (
    EveusSwitchEntityDescription(
        key="stop_charging",
        name="Stop Charging",
        icon="mdi:ev-station",
        entity_category=EntityCategory.CONFIG,
        command="evseEnabled",
        state_key="evseEnabled",
    ),
    EveusSwitchEntityDescription(
        key="one_charge",
        name="One Charge",
        icon="mdi:lightning-bolt",
        entity_category=EntityCategory.CONFIG,
        command="oneCharge",
        state_key="oneCharge",
    ),
    EveusSwitchEntityDescription(
        key="reset_counter_a",
        name="Reset Counter A",
        icon="mdi:refresh-circle",
        entity_category=EntityCategory.CONFIG,
        command="rstEM1",
        state_key="IEM1",
    ),
)


class BaseSwitchEntity(
    OptimisticControlMixin[bool],
    ControlEntityMixin,
    BaseEveusEntity,
    SwitchEntity,
):
    """Description-driven switch entity with optimistic UI state."""

    _control_entity_label = "Switch"

    def __init__(
        self,
        updater,
        entity_description: EveusSwitchEntityDescription,
        device_number: int = 1,
    ) -> None:
        """Initialize the switch."""
        self.entity_description = entity_description
        self.ENTITY_NAME = entity_description.name
        super().__init__(updater, device_number)
        self._command = entity_description.command
        self._state_key = entity_description.state_key
        self._pending_command: bool | None = None
        self._init_optimistic_control()
        self._attr_is_on = False

    @property
    def _optimistic_state(self) -> bool | None:
        """Compatibility alias for older tests and diagnostics."""
        return self._optimistic_value

    @_optimistic_state.setter
    def _optimistic_state(self, value: bool | None) -> None:
        self._optimistic_value = value

    @property
    def _optimistic_state_time(self) -> float:
        """Compatibility alias for older tests and diagnostics."""
        return self._optimistic_value_time

    @_optimistic_state_time.setter
    def _optimistic_state_time(self, value: float) -> None:
        self._optimistic_value_time = value

    @property
    def _last_device_state(self) -> bool | None:
        """Compatibility alias for older tests and diagnostics."""
        return self._last_device_value

    @_last_device_state.setter
    def _last_device_state(self, value: bool | None) -> None:
        self._last_device_value = value

    @property
    def is_on(self) -> bool:
        """Return cached switch state without side effects."""
        return bool(self._attr_is_on)

    async def async_added_to_hass(self) -> None:
        """Resolve the initial state after restore/coordinator data is available."""
        await super().async_added_to_hass()
        self._attr_is_on = self._resolve_state()

    def _resolve_state(self) -> bool:
        """Resolve switch state from optimistic, device, and restore state."""
        current_time = time.time()

        if self._optimistic_value_is_valid(current_time, OPTIMISTIC_CONTROL_TTL):
            return bool(self._optimistic_value)

        if self._updater.available and self._updater.data and self._state_key in self._updater.data:
            device_value = get_safe_value(self._updater.data, self._state_key, int, 0)
            return bool(device_value)

        if self._last_device_value is not None:
            if current_time - self._last_successful_read < CONTROL_GRACE_PERIOD:
                return self._last_device_value

        return False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self._async_send_command_or_raise(1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self._async_send_command_or_raise(0)

    async def _async_send_command(self, command_value: int) -> bool:
        """Send command with optimistic state."""
        self._pending_command = bool(command_value)
        self._attr_is_on = self._pending_command
        self.async_write_ha_state()

        try:
            success = await self._updater.send_command(self._command, command_value)
            if success:
                self._set_optimistic_value(bool(command_value))
            return success
        finally:
            self._pending_command = None
            self._last_command_time = time.time()
            self._attr_is_on = self._resolve_state()
            self.async_write_ha_state()

    async def _async_send_command_or_raise(self, command_value: int) -> None:
        """Send command and raise HomeAssistantError on failure so HA shows a toast."""
        success = await self._async_send_command(command_value)
        if not success:
            raise HomeAssistantError(
                f"Eveus charger did not accept '{self.name}' "
                f"{'on' if command_value else 'off'} command"
            )

    async def _async_restore_state(self, state: State) -> None:
        """Restore previous display state only; no commands sent on startup."""
        if state and state.state in ("on", "off"):
            self._last_device_value = state.state == "on"
            self._attr_is_on = self._last_device_value

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data and reconcile with device state."""
        self._maybe_finalize_device_info()
        self._update_availability_state()
        if self._pending_command is not None:
            return

        current_time = time.time()
        if self._updater.available and self._updater.data and self._state_key in self._updater.data:
            device_value = get_safe_value(self._updater.data, self._state_key, int, 0)
            self._reconcile_with_device(
                bool(device_value),
                current_time,
                lambda optimistic, device: optimistic == device,
            )

        self._expire_optimistic_value(current_time, OPTIMISTIC_CONTROL_TTL)
        self._attr_is_on = self._resolve_state()
        self.async_write_ha_state()


class BaseCounterSwitch(ControlEntityMixin, BaseEveusEntity, SwitchEntity):
    """Base class for counter-status switches."""

    _control_entity_label = "Switch"

    def __init__(
        self,
        updater,
        entity_description: EveusSwitchEntityDescription,
        device_number: int = 1,
    ) -> None:
        """Initialize the counter switch."""
        self.entity_description = entity_description
        self.ENTITY_NAME = entity_description.name
        super().__init__(updater, device_number)
        self._command = entity_description.command
        self._state_key = entity_description.state_key
        self._attr_is_on = False

    @property
    def is_on(self) -> bool:
        """Return cached counter state."""
        return bool(self._attr_is_on)

    def _resolve_state(self) -> bool:
        """Return True if the counter has a value."""
        if not self._updater.available or not self._updater.data:
            return False
        if self._state_key in self._updater.data:
            value = get_safe_value(self._updater.data, self._state_key, float, 0)
            return value > 0
        return False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """No action; switch represents counter status."""

    async def _async_restore_state(self, state: State) -> None:
        """No state restoration for counter switches."""

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data based on counter value."""
        self._maybe_finalize_device_info()
        self._update_availability_state()
        self._attr_is_on = self._resolve_state()
        self.async_write_ha_state()


class EveusResetCounterASwitch(BaseCounterSwitch):
    """Representation of Eveus reset counter A switch."""

    def __init__(self, updater, device_number: int = 1) -> None:
        """Initialize with special reset behavior."""
        super().__init__(updater, SWITCH_DESCRIPTIONS[2], device_number)
        self._safe_mode = True
        self._last_reset_time = 0.0
        self._reset_lock = asyncio.Lock()

    async def async_added_to_hass(self) -> None:
        """Handle entity addition with delayed safe mode disable."""
        await super().async_added_to_hass()
        self.async_on_remove(async_call_later(self.hass, 5, self._disable_safe_mode))

    @callback
    def _disable_safe_mode(self, _now=None) -> None:
        """Disable safe mode after first successful update."""
        self._safe_mode = False
        current_state = self._resolve_state()
        if self._attr_is_on != current_state:
            self._attr_is_on = current_state
            self.async_write_ha_state()

    def _resolve_state(self) -> bool:
        """Return True if counter A has a value."""
        if self._safe_mode:
            return False
        return super()._resolve_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Perform reset when turned off."""
        if self._safe_mode:
            return
        async with self._reset_lock:
            success = await self._updater.send_command(self._command, 0)
            if success:
                self._last_reset_time = time.time()
                _LOGGER.debug("Successfully reset counter A")
                return
            raise HomeAssistantError("Eveus charger did not accept reset Counter A command")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EveusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eveus switches."""
    runtime_data = entry.runtime_data
    updater = runtime_data.updater
    device_number = runtime_data.device_number

    switches = [
        BaseSwitchEntity(updater, description, device_number)
        for description in SWITCH_DESCRIPTIONS[:2]
    ]
    switches.append(EveusResetCounterASwitch(updater, device_number))

    async_add_entities(switches)
