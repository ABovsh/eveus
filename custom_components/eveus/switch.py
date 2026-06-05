"""Support for Eveus switches with optimistic UI and safety."""
from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EveusConfigEntry
from .common_base import (
    ControlEntityMixin,
    WriteOnChangeMixin,
)
from .control_base import CommandBackedEntity
from .const import CONTROL_GRACE_PERIOD, OPTIMISTIC_CONTROL_TTL
from .utils import get_safe_value

_LOGGER = logging.getLogger(__name__)


class EveusSwitchEntityDescription(SwitchEntityDescription, frozen_or_thawed=True):
    """Description for Eveus switch entities."""

    command: str
    state_key: str
    # Sibling form fields written alongside ``command`` in the same request,
    # each set to the same on/off value. Required for settings the firmware
    # only accepts as a bundled "save" form (e.g. OCPP needs ocppVendor).
    command_extra: tuple[str, ...] = ()


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
        key="adaptive_mode",
        name="Adaptive Mode",
        icon="mdi:auto-mode",
        entity_category=EntityCategory.CONFIG,
        command="aiStatus",
        state_key="aiStatus",
    ),
    EveusSwitchEntityDescription(
        key="schedule_1_enabled",
        name="Schedule 1 Enabled",
        icon="mdi:calendar-clock",
        entity_category=EntityCategory.CONFIG,
        command="sh1Enabled",
        state_key="sh1Enabled",
    ),
    EveusSwitchEntityDescription(
        key="schedule_2_enabled",
        name="Schedule 2 Enabled",
        icon="mdi:calendar-clock",
        entity_category=EntityCategory.CONFIG,
        command="sh2Enabled",
        state_key="sh2Enabled",
    ),
    EveusSwitchEntityDescription(
        key="ocpp",
        name="Connect to OCPP",
        icon="mdi:cloud-sync",
        entity_category=EntityCategory.CONFIG,
        command="ocppEnabled",
        state_key="ocppEnabled",
        command_extra=("ocppVendor",),
    ),
)


class BaseSwitchEntity(
    WriteOnChangeMixin,
    ControlEntityMixin,
    CommandBackedEntity[bool],
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
        self._command_extra = entity_description.command_extra
        self._pending_command: bool | None = None
        self._init_optimistic_control()
        self._attr_is_on = None
        self._init_write_on_change()

    @property
    def _optimistic_state(self) -> bool | None:
        """Test-facing alias for the canonical _optimistic_value attribute."""
        return self._optimistic_value

    @_optimistic_state.setter
    def _optimistic_state(self, value: bool | None) -> None:
        self._optimistic_value = value

    @property
    def _optimistic_state_time(self) -> float:
        """Test-facing alias for the canonical _optimistic_value_time attribute."""
        return self._optimistic_value_time

    @_optimistic_state_time.setter
    def _optimistic_state_time(self, value: float) -> None:
        self._optimistic_value_time = value

    @property
    def _last_device_state(self) -> bool | None:
        """Test-facing alias for the canonical _last_device_value attribute."""
        return self._last_device_value

    @_last_device_state.setter
    def _last_device_state(self, value: bool | None) -> None:
        self._last_device_value = value

    @property
    def is_on(self) -> bool | None:
        """Return cached switch state without side effects (None = unknown)."""
        return self._attr_is_on

    def _read_device_value(self) -> bool | None:
        """Return the latest valid switch state from coordinator data."""
        if not (
            self._updater.available
            and self._updater.data
            and self._state_key in self._updater.data
        ):
            return None
        device_value = get_safe_value(self._updater.data, self._state_key, int)
        if device_value in (0, 1):
            return bool(device_value)
        return None

    def _values_equal(self, optimistic: bool, device: bool) -> bool:
        """Return whether a device switch state confirms the optimistic value."""
        return optimistic == device

    def _resolve_display_value(self) -> bool | None:
        """Resolve the switch display value."""
        return self._resolve_state()

    def _set_display_value(self, value: bool | None) -> None:
        """Store the switch display value."""
        self._attr_is_on = value

    def _get_pending(self) -> bool | None:
        """Return the pending switch command sentinel."""
        return self._pending_command

    async def async_added_to_hass(self) -> None:
        """Resolve the initial state after restore/coordinator data is available."""
        await super().async_added_to_hass()
        self._attr_is_on = self._resolve_state()

    def _resolve_state(self) -> bool | None:
        """Resolve switch state from optimistic, device, and restore state.

        Returns None (unknown) rather than a definite ``off`` when no trusted
        source is available, so a missing/invalid payload field is not exposed
        as a real ``off`` state that automations could act on.
        """
        current_time = time.time()

        if self._optimistic_value_is_valid(current_time, OPTIMISTIC_CONTROL_TTL):
            return bool(self._optimistic_value)

        if self._updater.available and self._updater.data and self._state_key in self._updater.data:
            device_value = get_safe_value(self._updater.data, self._state_key, int)
            if device_value in (0, 1):
                return bool(device_value)

        if self._last_device_value is not None:
            if current_time - self._last_successful_read < CONTROL_GRACE_PERIOD:
                return self._last_device_value

        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self._async_send_command_or_raise(1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self._async_send_command_or_raise(0)

    async def _async_send_command(self, command_value: int) -> bool:
        """Send command with optimistic state."""
        async with self._command_lock:
            self._pending_command = bool(command_value)
            self._attr_is_on = self._pending_command
            self._write_if_changed(self._attr_is_on)

            try:
                extra = (
                    dict.fromkeys(self._command_extra, command_value)
                    if self._command_extra
                    else None
                )
                success = await self._updater.send_command(
                    self._command, command_value, extra=extra
                )
                if success:
                    self._set_optimistic_value(bool(command_value))
                return success
            finally:
                self._pending_command = None
                self._last_command_time = time.time()
                self._attr_is_on = self._resolve_state()
                self._write_if_changed(self._attr_is_on)

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
            self._last_successful_read = time.time()
            self._attr_is_on = self._last_device_value

async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EveusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eveus switches."""
    runtime_data = entry.runtime_data
    updater = runtime_data.updater
    device_number = runtime_data.device_number

    async_add_entities(
        BaseSwitchEntity(updater, description, device_number)
        for description in SWITCH_DESCRIPTIONS
    )
