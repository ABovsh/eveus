"""Support for Eveus number entities with optimistic UI and safety."""
from __future__ import annotations

import logging
import asyncio
import time
from typing import Any, Optional

from homeassistant.components.number import (
    NumberEntity,
    NumberMode,
    NumberDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback, State
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.const import (
    CONF_HOST,
    CONF_USERNAME,
    CONF_PASSWORD,
    UnitOfElectricCurrent,
)

from .const import (
    DOMAIN,
    MODEL_MAX_CURRENT,
    MIN_CURRENT,
    CONF_MODEL,
    CONTROL_GRACE_PERIOD,
)
from .common import BaseEveusEntity
from .utils import get_safe_value

_LOGGER = logging.getLogger(__name__)

# How long to trust user's command before requiring device confirmation
OPTIMISTIC_VALUE_TTL = 120  # 2 minutes - enough time for device to respond

class EveusNumberEntity(BaseEveusEntity, NumberEntity):
    """Base number entity with responsive UI and safety."""
    
    _attr_has_entity_name = True
    _attr_should_poll = False
    
    def __init__(self, updater, device_number: int = 1) -> None:
        """Initialize the entity."""
        super().__init__(updater, device_number)
        
        # State management for responsive UI
        self._pending_value: Optional[float] = None     # Value being set
        self._optimistic_value: Optional[float] = None  # What user just set (short-term)
        self._optimistic_value_time: float = 0          # When optimistic value was set
        self._last_device_value: Optional[float] = None # Last known device value
        self._last_command_time = 0
        self._last_successful_read = 0
        
    @property
    def available(self) -> bool:
        """Control entities use shorter grace period for safety."""
        if not self._updater.available:
            # Device offline - check how long
            current_time = time.time()
            if self._unavailable_since is None:
                self._unavailable_since = current_time
                return True  # Brief grace period starts
            
            # Use CONTROL_GRACE_PERIOD (30s) instead of regular grace period
            unavailable_duration = current_time - self._unavailable_since
            if unavailable_duration < CONTROL_GRACE_PERIOD:
                return True  # Still in short grace period
            else:
                # Grace period expired - mark unavailable and clear optimistic value
                if self._last_known_available and self._should_log_availability():
                    _LOGGER.info("Number %s unavailable (device offline %.0fs)", 
                               self.unique_id, unavailable_duration)
                self._last_known_available = False
                self._optimistic_value = None  # Clear optimistic value when offline
                return False
        
        # Device is available
        if self._unavailable_since is not None:
            if self._should_log_availability():
                _LOGGER.debug("Number %s connection restored", self.unique_id)
            self._unavailable_since = None
        self._last_known_available = True
        return True
    
    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()


class EveusCurrentNumber(EveusNumberEntity):
    """Representation of Eveus current control with responsive UI."""

    ENTITY_NAME = "Charging Current"
    _attr_native_step = 1.0
    _attr_mode = NumberMode.SLIDER
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_device_class = NumberDeviceClass.CURRENT
    _attr_icon = "mdi:current-ac"
    _attr_entity_category = EntityCategory.CONFIG
    _command = "currentSet"

    def __init__(self, updater, model: str, device_number: int = 1) -> None:
        """Initialize the current control."""
        super().__init__(updater, device_number)
        self._model = model
        self._command_lock = asyncio.Lock()
        
        # Set min/max values based on model
        self._attr_native_min_value = float(MIN_CURRENT)
        self._attr_native_max_value = float(MODEL_MAX_CURRENT[model])

    @property
    def native_value(self) -> float | None:
        """Return current value with optimistic UI for responsiveness."""
        current_time = time.time()
        
        # Priority 1: Command in progress (immediate feedback)
        if self._pending_value is not None:
            return self._pending_value
        
        # Priority 2: Recent optimistic value (user just set it, trust for 2 minutes)
        if self._optimistic_value is not None:
            time_since_set = current_time - self._optimistic_value_time
            if time_since_set < OPTIMISTIC_VALUE_TTL:
                # Still within optimistic window - show what user set
                return self._optimistic_value
            else:
                # Optimistic value expired - clear it
                self._optimistic_value = None
        
        # Priority 3: Current device value (the truth)
        if self._updater.available and self._updater.data:
            if self._command in self._updater.data:
                device_value = get_safe_value(self._updater.data, self._command, float)
                if device_value is not None:
                    new_device_value = float(device_value)
                    self._last_device_value = new_device_value
                    self._last_successful_read = current_time
                    
                    # If device value differs from optimistic, device value wins
                    if self._optimistic_value is not None and abs(self._optimistic_value - new_device_value) > 0.5:
                        _LOGGER.debug("Device value (%.1f) differs from optimistic (%.1f), clearing optimistic",
                                    new_device_value, self._optimistic_value)
                        self._optimistic_value = None
                    
                    return new_device_value
        
        # Priority 4: Recent device value (within grace period)
        if self._last_device_value is not None:
            time_since_read = current_time - self._last_successful_read
            if time_since_read < CONTROL_GRACE_PERIOD:
                return self._last_device_value
        
        # No valid value available
        return None

    async def async_set_native_value(self, value: float) -> None:
        """Set new current value with optimistic UI."""
        async with self._command_lock:
            try:
                # Clamp value to valid range
                clamped_value = max(self._attr_native_min_value, 
                                  min(self._attr_native_max_value, value))
                int_value = int(clamped_value)
                
                # Set pending value for immediate feedback
                self._pending_value = float(int_value)
                self.async_write_ha_state()
                
                # Send command
                success = await self._updater.send_command(self._command, int_value)
                
                if success:
                    # Command succeeded - set optimistic value (trust for 2 minutes)
                    self._optimistic_value = float(int_value)
                    self._optimistic_value_time = time.time()
                    _LOGGER.debug("Successfully set %s to %dA (optimistic for %ds)", 
                                self.name, int_value, OPTIMISTIC_VALUE_TTL)
                else:
                    # Command failed - don't set optimistic value
                    _LOGGER.warning("Failed to set %s to %dA", self.name, int_value)
                    
            except Exception as err:
                _LOGGER.error("Failed to set current value: %s", err)
            finally:
                # Clear pending value
                self._pending_value = None
                self._last_command_time = time.time()
                self.async_write_ha_state()

    async def _async_restore_state(self, state: State) -> None:
        """Restore previous value - apply when device available."""
        try:
            if state and state.state not in (None, 'unknown', 'unavailable'):
                restored_value = float(state.state)
                
                # Validate restored value is in range
                if self._attr_native_min_value <= restored_value <= self._attr_native_max_value:
                    _LOGGER.debug("Will attempt to restore %s to %.1fA when device available", 
                                self.name, restored_value)
                    
                    # Try to apply the restored value when device becomes available
                    self.hass.async_create_task(self._apply_restored_value(restored_value))
                else:
                    _LOGGER.warning("Restored value %.1fA for %s is out of range (%.1f-%.1f)", 
                                  restored_value, self.name, 
                                  self._attr_native_min_value, self._attr_native_max_value)
                    
        except (TypeError, ValueError) as err:
            _LOGGER.debug("Could not restore number state for %s: %s", self.name, err)

    async def _apply_restored_value(self, target_value: float) -> None:
        """Apply restored value when device becomes available."""
        # Wait for device to be ready
        await asyncio.sleep(5)
        
        # Only apply if device is available and we can verify current value
        if not self._updater.available or not self._updater.data:
            _LOGGER.debug("Device not available, skipping value restoration for %s", self.name)
            return
        
        if self._command not in self._updater.data:
            return
            
        # Get current device value
        current_device_value = get_safe_value(self._updater.data, self._command, float)
        
        # Only apply if different from device value (with tolerance)
        if current_device_value is None or abs(current_device_value - target_value) > 0.5:
            _LOGGER.info("Applying restored current value for %s: %.1fA", 
                       self.name, target_value)
            await self.async_set_native_value(target_value)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data - reconcile with device value."""
        current_time = time.time()
        
        # Update last device value from fresh data
        if self._updater.available and self._updater.data:
            if self._command in self._updater.data:
                device_value = get_safe_value(self._updater.data, self._command, float)
                if device_value is not None:
                    new_device_value = float(device_value)
                    self._last_device_value = new_device_value
                    self._last_successful_read = current_time
                    
                    # Reconcile optimistic value with device value
                    if self._optimistic_value is not None:
                        if abs(self._optimistic_value - new_device_value) < 0.5:
                            # Device confirmed our optimistic value - can clear it now
                            _LOGGER.debug("Device confirmed optimistic value for %s", self.name)
                            self._optimistic_value = None
                        else:
                            # Device value differs - check how long we've been waiting
                            time_since_set = current_time - self._optimistic_value_time
                            if time_since_set > 10:  # Give device 10 seconds to respond
                                _LOGGER.debug("Device value differs from optimistic after %ds for %s, trusting device",
                                            time_since_set, self.name)
                                self._optimistic_value = None
        
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Eveus number entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    updater = data.get("updater")
    device_number = data.get("device_number", 1)
    
    if not updater:
        _LOGGER.error("No updater found in data")
        return
        
    model = entry.data.get(CONF_MODEL)
    if not model:
        _LOGGER.error("No model specified in config")
        return

    entities = [
        EveusCurrentNumber(updater, model, device_number),
    ]

    # Initialize entities dict if needed
    if "entities" not in data:
        data["entities"] = {}
    
    data["entities"]["number"] = {
        entity.unique_id: entity for entity in entities
    }

    async_add_entities(entities)
