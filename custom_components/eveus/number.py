"""Support for Eveus number entities with optimistic UI and safety."""
from __future__ import annotations

import logging
import asyncio
import time
from typing import Optional

from homeassistant.components.number import (
    NumberEntity,
    NumberMode,
    NumberDeviceClass,
    NumberEntityDescription,
)
from homeassistant.core import HomeAssistant, callback, State
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.const import (
    UnitOfElectricCurrent,
)

from . import EveusConfigEntry
from .const import (
    MODEL_MAX_CURRENT,
    MIN_CURRENT,
    CONF_MODEL,
    CONTROL_GRACE_PERIOD,
)
from .common import BaseEveusEntity
from .utils import get_safe_value

_LOGGER = logging.getLogger(__name__)

# How long to trust user's command before requiring device confirmation
OPTIMISTIC_VALUE_TTL = 120

CHARGING_CURRENT_DESCRIPTION = NumberEntityDescription(
    key="charging_current",
    name="Charging Current",
    icon="mdi:current-ac",
    entity_category=EntityCategory.CONFIG,
    native_step=1.0,
    mode=NumberMode.SLIDER,
    native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
    device_class=NumberDeviceClass.CURRENT,
)


class EveusNumberEntity(BaseEveusEntity, NumberEntity):
    """Base number entity with responsive UI and safety."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        updater,
        entity_description: NumberEntityDescription,
        device_number: int = 1,
    ) -> None:
        """Initialize the entity."""
        self.entity_description = entity_description
        self.ENTITY_NAME = entity_description.name
        super().__init__(updater, device_number)

        self._pending_value: Optional[float] = None
        self._optimistic_value: Optional[float] = None
        self._optimistic_value_time: float = 0
        self._last_device_value: Optional[float] = None
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
                    "Number %s unavailable (device offline %.0fs)",
                    self.unique_id, unavailable_duration,
                )
            self._last_known_available = False
            self._optimistic_value = None
            return False

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
    _command = "currentSet"

    def __init__(self, updater, model: str, device_number: int = 1) -> None:
        """Initialize the current control."""
        super().__init__(updater, CHARGING_CURRENT_DESCRIPTION, device_number)
        self._model = model
        self._command_lock = asyncio.Lock()

        self._attr_native_min_value = float(MIN_CURRENT)
        self._attr_native_max_value = float(MODEL_MAX_CURRENT[model])

    @property
    def native_value(self) -> float | None:
        """Return current value with optimistic UI."""
        current_time = time.time()

        if self._pending_value is not None:
            return self._pending_value

        if self._optimistic_value is not None:
            if current_time - self._optimistic_value_time < OPTIMISTIC_VALUE_TTL:
                return self._optimistic_value
            self._optimistic_value = None

        if self._updater.available and self._updater.data:
            if self._command in self._updater.data:
                device_value = get_safe_value(self._updater.data, self._command, float)
                if device_value is not None:
                    new_device_value = float(device_value)
                    self._last_device_value = new_device_value
                    self._last_successful_read = current_time

                    if (
                        self._optimistic_value is not None
                        and abs(self._optimistic_value - new_device_value) > 0.5
                    ):
                        self._optimistic_value = None
                    return new_device_value

        if self._last_device_value is not None:
            if current_time - self._last_successful_read < CONTROL_GRACE_PERIOD:
                return self._last_device_value

        return None

    async def async_set_native_value(self, value: float) -> None:
        """Set new current value with optimistic UI."""
        async with self._command_lock:
            try:
                clamped_value = max(
                    self._attr_native_min_value,
                    min(self._attr_native_max_value, value),
                )
                int_value = int(clamped_value)

                self._pending_value = float(int_value)
                self.async_write_ha_state()

                success = await self._updater.send_command(self._command, int_value)

                if success:
                    self._optimistic_value = float(int_value)
                    self._optimistic_value_time = time.time()
                else:
                    _LOGGER.warning("Failed to set %s to %dA", self.name, int_value)

            except Exception as err:
                _LOGGER.error("Failed to set current value: %s", err)
            finally:
                self._pending_value = None
                self._last_command_time = time.time()
                self.async_write_ha_state()

    async def _async_restore_state(self, state: State) -> None:
        """Restore previous display value only — no commands sent on startup."""
        try:
            if state and state.state not in (None, "unknown", "unavailable"):
                restored_value = float(state.state)
                if self._attr_native_min_value <= restored_value <= self._attr_native_max_value:
                    self._last_device_value = restored_value
        except (TypeError, ValueError) as err:
            _LOGGER.debug("Could not restore number state for %s: %s", self.name, err)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data — reconcile with device value."""
        current_time = time.time()

        if self._updater.available and self._updater.data:
            if self._command in self._updater.data:
                device_value = get_safe_value(self._updater.data, self._command, float)
                if device_value is not None:
                    new_device_value = float(device_value)
                    self._last_device_value = new_device_value
                    self._last_successful_read = current_time

                    if self._optimistic_value is not None:
                        if abs(self._optimistic_value - new_device_value) < 0.5:
                            self._optimistic_value = None
                        elif current_time - self._optimistic_value_time > 10:
                            self._optimistic_value = None

        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EveusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Eveus number entities."""
    runtime_data = entry.runtime_data
    updater = runtime_data.updater
    device_number = runtime_data.device_number

    model = entry.data.get(CONF_MODEL)
    if not model:
        _LOGGER.error("No model specified in config")
        return

    entities = [
        EveusCurrentNumber(updater, model, device_number),
    ]

    async_add_entities(entities)
