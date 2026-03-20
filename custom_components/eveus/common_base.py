"""Base entity classes for Eveus integration with enhanced state persistence."""
import logging
import time
import asyncio
from typing import Any, Optional

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import EntityCategory

from .utils import get_device_info, get_device_suffix
from .const import AVAILABILITY_GRACE_PERIOD, ERROR_LOG_RATE_LIMIT, STATE_CACHE_TTL

_LOGGER = logging.getLogger(__name__)

# Type checking imports to avoid circular imports
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .common_network import EveusUpdater


class BaseEveusEntity(RestoreEntity, Entity):
    """Base implementation for Eveus entities with enhanced state persistence."""

    ENTITY_NAME: str = None
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, updater, device_number: int = 1) -> None:
        """Initialize the entity."""
        super().__init__()
        self._updater = updater
        self._device_number = device_number
        self._updater.register_entity(self)
        
        # State persistence tracking
        self._state_restored = False
        self._restore_in_progress = False
        
        # Enhanced availability tracking with grace period (WiFi optimized)
        self._last_available_log = 0
        self._last_known_available = True
        self._unavailable_since = None  # Track when device became unavailable
        
        # State caching for brief WiFi outages (60 seconds)
        self._cached_data = None
        self._cached_data_time = 0

        if self.ENTITY_NAME is None:
            raise NotImplementedError("ENTITY_NAME must be defined in child class")

        self._attr_name = self.ENTITY_NAME
        
        # Generate unique ID with device suffix for multi-device support
        device_suffix = get_device_suffix(device_number)
        entity_key = self.ENTITY_NAME.lower().replace(' ', '_')
        self._attr_unique_id = f"eveus{device_suffix}_{entity_key}"

    @property
    def available(self) -> bool:
        """Return if entity is available with grace period support (WiFi optimized)."""
        current_updater_available = self._updater.available
        current_time = time.time()
        
        # Device is available - reset unavailable tracking
        if current_updater_available:
            if self._unavailable_since is not None:
                # Log restoration if we should
                if self._should_log_availability():
                    _LOGGER.debug("Entity %s connection restored", self.unique_id)
                self._unavailable_since = None
            
            self._last_known_available = True
            return True
        
        # Device reports unavailable - check grace period
        if self._unavailable_since is None:
            # First time unavailable - start grace period
            self._unavailable_since = current_time
            return True  # Still show as available during grace period
        
        # Check if grace period has expired (60 seconds for WiFi stability)
        unavailable_duration = current_time - self._unavailable_since
        if unavailable_duration < AVAILABILITY_GRACE_PERIOD:
            # Still in grace period
            return True
        else:
            # Grace period expired - mark as unavailable
            if self._last_known_available and self._should_log_availability():
                _LOGGER.info("Entity %s unavailable after grace period (%.0fs)", 
                           self.unique_id, unavailable_duration)
            self._last_known_available = False
            
            # Clear cached data when grace period expires to ensure entity shows unavailable
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
        """Get value from current data or cached data as fallback (WiFi optimized - 60 seconds)."""
        # Try current data first
        if self._updater.data and key in self._updater.data:
            # Update cache with fresh data
            self._cached_data = self._updater.data.copy()
            self._cached_data_time = time.time()
            return self._updater.data[key]
        
        # Fall back to cached data if recent enough (60 seconds for WiFi stability)
        if (self._cached_data and key in self._cached_data and 
            time.time() - self._cached_data_time < STATE_CACHE_TTL):
            return self._cached_data[key]
        
        return default

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device information."""
        try:
            # Use cached data if main data unavailable
            data_source = self._updater.data
            if not data_source and self._cached_data:
                data_source = self._cached_data
                
            return get_device_info(self._updater.host, data_source or {}, self._device_number)
        except Exception as err:
            # Gracefully handle device info errors when offline
            if self._should_log_availability():
                _LOGGER.debug("Error getting device info for %s: %s", self.unique_id, err)
            # Return minimal device info when offline
            device_suffix = "" if self._device_number == 1 else f" {self._device_number}"
            return {
                "identifiers": {("eveus", f"{self._updater.host}_{self._device_number}")},
                "name": f"Eveus EV Charger{device_suffix}",
                "manufacturer": "Eveus",
                "model": "Eveus EV Charger",
            }

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added with enhanced state restoration."""
        await super().async_added_to_hass()
        
        # Start the updater first
        try:
            await self._updater.async_start_updates()
        except Exception as err:
            _LOGGER.debug("Could not start updates for %s: %s", self.unique_id, err)
        
        # Then handle state restoration with proper timing
        try:
            self._restore_in_progress = True
            state = await self.async_get_last_state()
            if state:
                _LOGGER.debug("Restoring state for %s: %s", self.unique_id, state.state)
                await self._async_restore_state(state)
                self._state_restored = True
            else:
                _LOGGER.debug("No previous state found for %s", self.unique_id)
        except Exception as err:
            _LOGGER.debug("Could not restore state for %s: %s", self.unique_id, err)
        finally:
            self._restore_in_progress = False

    async def _async_restore_state(self, state) -> None:
        """Restore previous state - overridden by child classes."""
        pass

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        try:
            # Update cache when we get fresh data
            if self._updater.data:
                self._cached_data = self._updater.data.copy()
                self._cached_data_time = time.time()
                
            self.async_write_ha_state()
        except Exception as err:
            # Gracefully handle state write errors (device might be offline)
            _LOGGER.debug("Could not write state for %s: %s", self.unique_id, err)

    async def _wait_for_device_ready(self, timeout: int = 30) -> bool:
        """Wait for device to be ready for commands."""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if self._updater.available and (self._updater.data or self._cached_data):
                # Give a bit more time for data to stabilize
                await asyncio.sleep(2)
                return True
            await asyncio.sleep(1)
            
        return False


class EveusSensorBase(BaseEveusEntity, SensorEntity):
    """Base sensor entity for Eveus with SIMPLE offline handling.
    
    SIMPLE FIX: Sensors have NO grace period. They immediately show as unavailable when offline.
    """
    
    def __init__(self, updater: "EveusUpdater", device_number: int = 1) -> None:
        """Initialize the sensor."""
        super().__init__(updater, device_number)
        self._attr_native_value = None
        self._last_valid_value = None
        self._last_error_log = 0

    @property
    def available(self) -> bool:
        """SIMPLE FIX: Sensors are immediately unavailable when updater is offline.
        
        NO grace period for sensors - they should immediately reflect offline state.
        Grace period is only for control entities (switches, numbers) for safety.
        """
        # SIMPLE: Return updater availability directly - no grace period for sensors
        return self._updater.available

    @property
    def native_value(self) -> Any:
        """Return sensor value - None when unavailable."""
        # When unavailable, return None so HA shows "Unavailable"
        if not self.available:
            return None
            
        try:
            value = self._get_sensor_value()
            if value is not None:
                self._last_valid_value = value
            return value
        except Exception as err:
            # Rate limit error logs
            current_time = time.time()
            if current_time - self._last_error_log > ERROR_LOG_RATE_LIMIT:
                self._last_error_log = current_time
                _LOGGER.debug("Error getting sensor value for %s: %s", self.unique_id, err)
            # Return None when there's an error
            return None

    def _get_sensor_value(self) -> Any:
        """Get sensor value - to be overridden by subclasses."""
        return self._attr_native_value


class EveusDiagnosticSensor(EveusSensorBase):
    """Base diagnostic sensor with stable handling."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:information"
