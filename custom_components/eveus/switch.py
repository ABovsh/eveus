"""Support for Eveus switches with optimistic UI and safety."""
from __future__ import annotations

import logging
import asyncio
import time
from typing import Any, Optional

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN, CONTROL_GRACE_PERIOD
from .common import BaseEveusEntity
from .utils import get_safe_value

_LOGGER = logging.getLogger(__name__)

# How long to trust user's command before requiring device confirmation
OPTIMISTIC_STATE_TTL = 120


class BaseSwitchEntity(BaseEveusEntity, SwitchEntity):
    """Base switch entity with responsive UI and safety."""

    _attr_entity_category = EntityCategory.CONFIG
    _command: str = None
    _state_key: str = None

    def __init__(self, updater, device_number: int = 1) -> None:
        """Initialize the switch."""
        super().__init__(updater, device_number)
        self._command_lock = asyncio.Lock()

        # State management for responsive UI
        self._pending_command: Optional[bool] = None
        self._optimistic_state: Optional[bool] = None
        self._optimistic_state_time: float = 0
        self._last_device_state: Optional[bool] = None
        self._last_command_time = 0
        self._last_successful_read = 0

    @property
    def available(self) -> bool:
        """Control entities use shorter grace period for safety."""
        if not self._updater.available:
            current_time = time.time()
            if self._unavailable_since is None:
                self._unavailable_since = current_time
                return True

            unavailable_duration = current_time - self._unavailable_since
            if unavailable_duration < CONTROL_GRACE_PERIOD:
                return True

            if self._last_known_available and self._should_log_availability():
                _LOGGER.info(
                    "Switch %s unavailable (device offline %.0fs)",
                    self.unique_id, unavailable_duration,
                )
            self._last_known_available = False
            self._optimistic_state = None
            return False

        if self._unavailable_since is not None:
            if self._should_log_availability():
                _LOGGER.debug("Switch %s connection restored", self.unique_id)
            self._unavailable_since = None
        self._last_known_available = True
        return True

    @property
    def is_on(self) -> bool:
        """Return switch state with optimistic UI."""
        current_time = time.time()

        if self._pending_command is not None:
            return self._pending_command

        if self._optimistic_state is not None:
            if current_time - self._optimistic_state_time < OPTIMISTIC_STATE_TTL:
                return self._optimistic_state
            self._optimistic_state = None

        if self._updater.available and self._updater.data:
            if self._state_key in self._updater.data:
                device_value = get_safe_value(self._updater.data, self._state_key, int, 0)
                new_device_state = bool(device_value)
                self._last_device_state = new_device_state
                self._last_successful_read = current_time

                if self._optimistic_state is not None and self._optimistic_state != new_device_state:
                    self._optimistic_state = None
                return new_device_state

        if self._last_device_state is not None:
            if current_time - self._last_successful_read < CONTROL_GRACE_PERIOD:
                return self._last_device_state

        return False

    async def _async_send_command(self, command_value: int) -> bool:
        """Send command with optimistic state."""
        async with self._command_lock:
            self._pending_command = bool(command_value)
            self.async_write_ha_state()

            try:
                success = await self._updater.send_command(self._command, command_value)

                if success:
                    self._optimistic_state = bool(command_value)
                    self._optimistic_state_time = time.time()
                else:
                    _LOGGER.warning(
                        "Failed to set %s to %s",
                        self.name, "on" if command_value else "off",
                    )
                return success

            finally:
                self._pending_command = None
                self._last_command_time = time.time()
                self.async_write_ha_state()

    async def _async_restore_state(self, state: State) -> None:
        """Restore previous display state only — no commands sent on startup."""
        if state and state.state in ("on", "off"):
            self._last_device_state = state.state == "on"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data — reconcile with device state."""
        current_time = time.time()

        if self._updater.available and self._updater.data:
            if self._state_key in self._updater.data:
                device_value = get_safe_value(self._updater.data, self._state_key, int, 0)
                new_device_state = bool(device_value)
                self._last_device_state = new_device_state
                self._last_successful_read = current_time

                if self._optimistic_state is not None:
                    if self._optimistic_state == new_device_state:
                        self._optimistic_state = None
                    elif current_time - self._optimistic_state_time > 10:
                        self._optimistic_state = None

        self.async_write_ha_state()


class EveusStopChargingSwitch(BaseSwitchEntity):
    """Representation of Eveus charging control switch."""

    ENTITY_NAME = "Stop Charging"
    _attr_icon = "mdi:ev-station"
    _command = "evseEnabled"
    _state_key = "evseEnabled"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on charging (enable EVSE)."""
        await self._async_send_command(1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off charging (disable EVSE)."""
        await self._async_send_command(0)


class EveusOneChargeSwitch(BaseSwitchEntity):
    """Representation of Eveus one charge switch."""

    ENTITY_NAME = "One Charge"
    _attr_icon = "mdi:lightning-bolt"
    _command = "oneCharge"
    _state_key = "oneCharge"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable one charge mode."""
        await self._async_send_command(1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable one charge mode."""
        await self._async_send_command(0)


class EveusResetCounterASwitch(BaseSwitchEntity):
    """Representation of Eveus reset counter A switch."""

    ENTITY_NAME = "Reset Counter A"
    _attr_icon = "mdi:refresh-circle"
    _command = "rstEM1"
    _state_key = "IEM1"

    def __init__(self, updater, device_number: int = 1) -> None:
        """Initialize with special reset behavior."""
        super().__init__(updater, device_number)
        self._safe_mode = True
        self._last_reset_time = 0

    async def async_added_to_hass(self) -> None:
        """Handle entity addition with delayed safe mode disable."""
        await super().async_added_to_hass()
        self.hass.async_create_task(self._disable_safe_mode())

    async def _disable_safe_mode(self) -> None:
        """Disable safe mode after first successful update."""
        await self._updater.async_start_updates()
        await asyncio.sleep(5)
        self._safe_mode = False

    @property
    def is_on(self) -> bool:
        """Return True if counter has a value."""
        if self._safe_mode:
            return False
        if not self._updater.available or not self._updater.data:
            return False
        if self._state_key in self._updater.data:
            value = get_safe_value(self._updater.data, self._state_key, float, 0)
            return value > 0
        return False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """No action — switch represents counter status."""

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Perform reset when turned off."""
        if self._safe_mode:
            return
        success = await self._updater.send_command(self._command, 0)
        if success:
            self._last_reset_time = time.time()
            _LOGGER.info("Successfully reset counter A")
        else:
            _LOGGER.warning("Failed to reset counter A")

    async def _async_restore_state(self, state: State) -> None:
        """No state restoration for reset switch."""

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data — state based on counter value."""
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eveus switches."""
    data = hass.data[DOMAIN][entry.entry_id]
    updater = data["updater"]
    device_number = data.get("device_number", 1)

    switches = [
        EveusStopChargingSwitch(updater, device_number),
        EveusOneChargeSwitch(updater, device_number),
        EveusResetCounterASwitch(updater, device_number),
    ]

    if "entities" not in data:
        data["entities"] = {}

    data["entities"]["switch"] = {
        switch.unique_id: switch for switch in switches
    }

    async_add_entities(switches)
