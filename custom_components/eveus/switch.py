"""Support for Eveus switches."""
from __future__ import annotations

import logging
import asyncio
import time
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from .common import BaseEveusEntity
from .utils import get_safe_value

_LOGGER = logging.getLogger(__name__)

class BaseSwitchEntity(BaseEveusEntity, SwitchEntity):
    """Base switch entity for Eveus."""

    _attr_entity_category = EntityCategory.CONFIG
    _command: str = None
    _state_key: str = None

    def __init__(self, updater) -> None:
        """Initialize the switch."""
        super().__init__(updater)
        self._command_lock = asyncio.Lock()
        self._pending_state = None

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        # Use pending state if available, otherwise check device state
        if self._pending_state is not None:
            return self._pending_state
        return bool(get_safe_value(self._updater.data, self._state_key, int, 0))

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return super().available and self._state_key in self._updater.data

    async def _async_send_command(self, command_value: int) -> None:
        """Send command to device."""
        async with self._command_lock:
            # Set pending state before sending command
            self._pending_state = bool(command_value)
            self.async_write_ha_state()
            
            # Send command
            await self._updater.send_command(self._command, command_value)

    async def _async_restore_state(self, state: State) -> None:
        """Restore previous state."""
        try:
            if state.state == "on":
                await self._async_send_command(1)
            else:
                await self._async_send_command(0)
        except Exception as err:
            _LOGGER.error("Error restoring state for %s: %s", self.name, err)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Clear pending state and use actual device state
        self._pending_state = None
        self.async_write_ha_state()


class EveusStopChargingSwitch(BaseSwitchEntity):
    """Representation of Eveus charging control switch."""

    ENTITY_NAME = "Stop Charging"
    _attr_icon = "mdi:ev-station"
    _command = "evseEnabled"
    _state_key = "evseEnabled"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on charging."""
        await self._async_send_command(1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off charging."""
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
    """Representation of Eveus reset counter A switch with direct reset action."""

    ENTITY_NAME = "Reset Counter A"
    _attr_icon = "mdi:refresh-circle"
    _command = "rstEM1"
    _state_key = "IEM1"

    def __init__(self, updater) -> None:
        """Initialize with safety flags."""
        super().__init__(updater)
        self._safe_mode = True
        self._last_reset_time = 0

    async def async_added_to_hass(self) -> None:
        """Handle entity addition with delayed safe mode disable."""
        await super().async_added_to_hass()
        self.hass.async_create_task(self._disable_safe_mode())

    async def _disable_safe_mode(self) -> None:
        """Disable safe mode after first successful update."""
        await self._updater.async_start_updates()
        await asyncio.sleep(5)  # Give time for first update
        self._safe_mode = False

    @property
    def is_on(self) -> bool:
        """Return True if counter has a value."""
        if self._safe_mode:
            return False
            
        value = get_safe_value(self._updater.data, self._state_key, float, 0)
        return value > 0

    async def async_turn_on(self, **kwargs: Any) -> None:
        """No action needed for turn_on - switch state represents counter status."""
        # Do nothing on turn_on as it's just a representation of counter status
        pass

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Perform reset command whenever switch is turned off."""
        if self._safe_mode:
            return

        # Always send the reset command when turned off, regardless of previous state
        if await self._updater.send_command(self._command, 0):
            # Reset happened; the counter value should update in the next data refresh
            self._last_reset_time = time.time()
            self.async_write_ha_state()

    async def _async_restore_state(self, state: State) -> None:
        """No action needed on restore - state is determined by counter value."""
        # Let the normal is_on logic determine the state based on counter value
        pass


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eveus switches."""
    data = hass.data[DOMAIN][entry.entry_id]
    updater = data["updater"]

    switches = [
        EveusStopChargingSwitch(updater),
        EveusOneChargeSwitch(updater),
        EveusResetCounterASwitch(updater),
    ]

    if "entities" not in data:
        data["entities"] = {}

    data["entities"]["switch"] = {
        switch.unique_id: switch for switch in switches
    }

    async_add_entities(switches)
