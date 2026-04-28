"""Base entity classes for Eveus integration."""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import State, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import AVAILABILITY_GRACE_PERIOD, ERROR_LOG_RATE_LIMIT, STATE_CACHE_TTL
from .utils import get_device_info, get_device_suffix

if TYPE_CHECKING:
    from .common_network import EveusUpdater

_LOGGER = logging.getLogger(__name__)


class BaseEveusEntity(CoordinatorEntity["EveusUpdater"], RestoreEntity):
    """Base implementation for Eveus entities with state persistence."""

    ENTITY_NAME: str | None = None
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, updater: "EveusUpdater", device_number: int = 1) -> None:
        """Initialize the entity."""
        super().__init__(updater)
        self._updater = updater
        self._device_number = device_number

        self._state_restored = False
        self._last_available_log = 0.0
        self._last_known_available = True
        self._unavailable_since: float | None = None
        self._cached_data: dict[str, Any] | None = None
        self._cached_data_time = 0.0

        if self.ENTITY_NAME is None:
            raise NotImplementedError("ENTITY_NAME must be defined in child class")

        self._attr_name = self.ENTITY_NAME
        device_suffix = get_device_suffix(device_number)
        entity_key = self.ENTITY_NAME.lower().replace(" ", "_")
        self._attr_unique_id = f"eveus{device_suffix}_{entity_key}"

    @property
    def available(self) -> bool:
        """Return if entity is available with grace period."""
        current_time = time.time()

        if self._updater.available:
            if self._unavailable_since is not None:
                if self._should_log_availability():
                    _LOGGER.debug("Entity %s connection restored", self.unique_id)
                self._unavailable_since = None
            self._last_known_available = True
            return True

        if self._unavailable_since is None:
            self._unavailable_since = current_time
            return True

        unavailable_duration = current_time - self._unavailable_since
        if unavailable_duration < AVAILABILITY_GRACE_PERIOD:
            return True

        if self._last_known_available and self._should_log_availability():
            _LOGGER.info(
                "Entity %s unavailable after grace period (%.0fs)",
                self.unique_id,
                unavailable_duration,
            )
        self._last_known_available = False
        self._cached_data = None
        self._cached_data_time = 0
        return False

    def _should_log_availability(self) -> bool:
        """Rate limit availability logging."""
        current_time = time.time()
        if current_time - self._last_available_log > ERROR_LOG_RATE_LIMIT:
            self._last_available_log = current_time
            return True
        return False

    def get_cached_data_value(self, key: str, default: Any = None) -> Any:
        """Get value from current data or cached data as fallback."""
        data = self._updater.data or {}
        if key in data:
            self._cached_data = data
            self._cached_data_time = time.time()
            return data[key]

        if (
            self._cached_data
            and key in self._cached_data
            and time.time() - self._cached_data_time < STATE_CACHE_TTL
        ):
            return self._cached_data[key]

        return default

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device information."""
        try:
            data_source = self._updater.data or self._cached_data or {}
            return get_device_info(self._updater.host, data_source, self._device_number)
        except Exception as err:
            if self._should_log_availability():
                _LOGGER.debug("Error getting device info for %s: %s", self.unique_id, err)
            device_suffix = "" if self._device_number == 1 else f" {self._device_number}"
            return {
                "identifiers": {("eveus", f"{self._updater.host}_{self._device_number}")},
                "name": f"Eveus EV Charger{device_suffix}",
                "manufacturer": "Eveus",
                "model": "Eveus EV Charger",
            }

    async def async_added_to_hass(self) -> None:
        """Handle entity addition with state restoration."""
        await super().async_added_to_hass()

        try:
            state = await self.async_get_last_state()
            if state:
                _LOGGER.debug("Restoring state for %s: %s", self.unique_id, state.state)
                await self._async_restore_state(state)
                self._state_restored = True
        except Exception as err:
            _LOGGER.debug("Could not restore state for %s: %s", self.unique_id, err)

    async def _async_restore_state(self, state: State) -> None:
        """Restore previous state - overridden by child classes."""

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()


class EveusSensorBase(BaseEveusEntity, SensorEntity):
    """Base sensor entity."""

    def __init__(self, updater: "EveusUpdater", device_number: int = 1) -> None:
        """Initialize the sensor."""
        super().__init__(updater, device_number)
        self._attr_native_value = None
        self._last_valid_value = None
        self._last_error_log = 0.0

    @property
    def available(self) -> bool:
        """Sensors are immediately unavailable when the coordinator fails."""
        return self._updater.available

    @property
    def native_value(self) -> Any:
        """Return sensor value."""
        if not self.available:
            return None

        try:
            value = self._get_sensor_value()
            if value is not None:
                self._last_valid_value = value
            return value
        except Exception as err:
            current_time = time.time()
            if current_time - self._last_error_log > ERROR_LOG_RATE_LIMIT:
                self._last_error_log = current_time
                _LOGGER.debug("Error getting sensor value for %s: %s", self.unique_id, err)
            return None

    def _get_sensor_value(self) -> Any:
        """Get sensor value - overridden by subclasses."""
        return self._attr_native_value


class EveusDiagnosticSensor(EveusSensorBase):
    """Base diagnostic sensor for backward compatibility."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:information"
