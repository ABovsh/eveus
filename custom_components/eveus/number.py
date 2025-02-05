"""Support for Eveus number entities."""
from __future__ import annotations

import logging
import asyncio
from typing import Any

from homeassistant.components.number import (
    NumberEntity,
    NumberMode,
    NumberDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
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
)
from .common import (
    BaseEveusEntity,
    EveusUpdater,
    send_eveus_command,
)

_LOGGER = logging.getLogger(__name__)

class EveusNumberEntity(BaseEveusEntity, NumberEntity):
    """Base number entity for Eveus."""
    
    _attr_has_entity_name = True
    _attr_should_poll = False
    
    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the entity."""
        super().__init__(updater)
        self._attr_native_value = None
        
    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state:
            await self._async_restore_state(state)
        
    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()

class EveusCurrentNumber(EveusNumberEntity):
    """Representation of Eveus current control."""

    ENTITY_NAME = "Charging Current"
    _attr_native_step = 1.0
    _attr_mode = NumberMode.SLIDER
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_device_class = NumberDeviceClass.CURRENT
    _attr_icon = "mdi:current-ac"
    _attr_entity_category = EntityCategory.CONFIG
    _command = "currentSet"

    def __init__(self, updater: EveusUpdater, model: str) -> None:
        """Initialize the current control."""
        super().__init__(updater)
        self._model = model
        self._command_lock = asyncio.Lock()
        
        # Set min/max values based on model
        self._attr_native_min_value = float(MIN_CURRENT)
        self._attr_native_max_value = float(MODEL_MAX_CURRENT[model])
        self._attr_native_value = None  # Will be set from device state

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        try:
            value = self._updater.data.get(self._command)
            if value is not None:
                self._attr_native_value = float(value)
            return self._attr_native_value
        except (TypeError, ValueError) as err:
            _LOGGER.error("Error getting current value: %s", err)
            return self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        """Set new current value."""
        async with self._command_lock:
            try:
                value = int(min(self._attr_native_max_value, max(self._attr_native_min_value, value)))
                if await self._updater.send_command(self._command, value):
                    self._attr_native_value = float(value)
                    self.async_write_ha_state()
            except Exception as err:
                _LOGGER.error("Failed to set current value: %s", err)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        try:
            value = self._updater.data.get(self._command)
            if value is not None:
                self._attr_native_value = float(value)
            self.async_write_ha_state()
        except (TypeError, ValueError) as err:
            _LOGGER.error("Error handling update: %s", err)

    async def _async_restore_state(self, state) -> None:
        """Restore previous state."""
        try:
            if state and state.state not in (None, 'unknown', 'unavailable'):
                restored_value = float(state.state)
                if self._attr_native_min_value <= restored_value <= self._attr_native_max_value:
                    await self.async_set_native_value(restored_value)
        except (TypeError, ValueError) as err:
            _LOGGER.warning("Could not restore number state: %s", err)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Eveus number entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    updater = data.get("updater")
    
    if not updater:
        _LOGGER.error("No updater found in data")
        return
        
    model = entry.data.get(CONF_MODEL)
    if not model:
        _LOGGER.error("No model specified in config")
        return

    entities = [
        EveusCurrentNumber(updater, model),
    ]

    # Initialize entities dict if needed
    if "entities" not in data:
        data["entities"] = {}
    
    data["entities"]["number"] = {
        entity.unique_id: entity for entity in entities
    }

    async_add_entities(entities)
