"""Base entity classes for Eveus integration."""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import State, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    AVAILABILITY_GRACE_PERIOD,
    CONTROL_GRACE_PERIOD,
    ERROR_LOG_RATE_LIMIT,
    STATE_CACHE_TTL,
)
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
        self._entity_available = True
        self._cached_data: dict[str, Any] | None = None
        self._cached_data_time = 0.0

        if self.ENTITY_NAME is None:
            raise NotImplementedError("ENTITY_NAME must be defined in child class")

        self._attr_name = self.ENTITY_NAME
        device_suffix = get_device_suffix(device_number)
        entity_key = self.ENTITY_NAME.lower().replace(" ", "_")
        self._attr_unique_id = f"eveus{device_suffix}_{entity_key}"
        self._refresh_cached_data()
        self._attr_device_info = self._build_device_info()
        self._device_info_finalized = self._device_info_has_firmware()

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self._entity_available

    def _update_availability_state(
        self,
        *,
        grace_period: int = AVAILABILITY_GRACE_PERIOD,
        label: str = "Entity",
        clear_optimistic_state: bool = False,
    ) -> bool:
        """Update availability state from coordinator data.

        Returns True when the visible availability changed.
        """
        previous_available = self._entity_available
        current_time = time.time()

        if self._updater.available:
            self._refresh_cached_data()
            if self._unavailable_since is not None:
                if self._should_log_availability():
                    _LOGGER.debug("%s %s connection restored", label, self.unique_id)
                self._unavailable_since = None
            self._last_known_available = True
            self._entity_available = True
            return previous_available != self._entity_available

        if self._unavailable_since is None:
            self._unavailable_since = current_time
            self._entity_available = True
            return previous_available != self._entity_available

        unavailable_duration = current_time - self._unavailable_since
        if unavailable_duration < grace_period:
            self._entity_available = True
            return previous_available != self._entity_available

        if self._last_known_available and self._should_log_availability():
            _LOGGER.debug(
                "%s %s unavailable after grace period (%.0fs)",
                label,
                self.unique_id,
                unavailable_duration,
            )
        self._last_known_available = False
        self._cached_data = None
        self._cached_data_time = 0
        if clear_optimistic_state:
            clear = getattr(self, "_clear_optimistic_state", None)
            if callable(clear):
                clear()
        self._entity_available = False
        return previous_available != self._entity_available

    def _should_log_availability(self) -> bool:
        """Rate limit availability logging."""
        current_time = time.time()
        if current_time - self._last_available_log > ERROR_LOG_RATE_LIMIT:
            self._last_available_log = current_time
            return True
        return False

    def _refresh_cached_data(self) -> None:
        """Snapshot coordinator data for short fallback periods."""
        if isinstance(self._updater.data, dict) and self._updater.data:
            self._cached_data = dict(self._updater.data)
            self._cached_data_time = time.time()

    def get_cached_data_value(self, key: str, default: Any = None) -> Any:
        """Get value from current data or cached data as fallback without mutation."""
        data = self._updater.data or {}
        if key in data:
            return data[key]

        if (
            self._cached_data
            and key in self._cached_data
            and time.time() - self._cached_data_time < STATE_CACHE_TTL
        ):
            return self._cached_data[key]

        return default

    def _build_device_info(self) -> dict[str, Any]:
        """Build device information from the latest available snapshot."""
        data = self._updater.data if isinstance(self._updater.data, dict) else None
        return get_device_info(
            self._updater.host,
            data or self._cached_data or {},
            self._device_number,
        )

    def _device_info_has_firmware(self) -> bool:
        """Whether the cached device_info already carries real firmware."""
        info = self._attr_device_info or {}
        sw = info.get("sw_version")
        return bool(sw) and sw != "Unknown"

    def _maybe_finalize_device_info(self) -> None:
        """Refresh device_info once firmware first becomes known.

        device_info is built once in __init__ for performance, but if the very
        first refresh returned an empty payload (charger offline at HA boot),
        sw_version stays "Unknown" forever. Once a real firmware string lands
        in coordinator data, rebuild and propagate it to the device registry.
        """
        if self._device_info_finalized:
            return
        if not self._updater.data:
            return

        new_info = self._build_device_info()
        if not new_info.get("sw_version") or new_info["sw_version"] == "Unknown":
            return

        self._attr_device_info = new_info
        self._device_info_finalized = True

        if self.hass is None:
            return
        registry = dr.async_get(self.hass)
        identifiers = new_info.get("identifiers")
        if not identifiers:
            return
        device = registry.async_get_device(identifiers=identifiers)
        if device is None:
            return
        registry.async_update_device(
            device.id,
            sw_version=new_info["sw_version"],
            model=new_info.get("model"),
            manufacturer=new_info.get("manufacturer"),
        )

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
            _LOGGER.debug(
                "Could not restore state for %s: %s",
                self.unique_id,
                err,
                exc_info=True,
            )

    async def _async_restore_state(self, state: State) -> None:
        """Restore previous state - overridden by child classes."""

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._maybe_finalize_device_info()
        if self._update_availability_state():
            self.async_write_ha_state()


class ControlEntityMixin:
    """Availability behavior for command-capable entities."""

    _control_entity_label = "Entity"

    @property
    def available(self) -> bool:
        """Control entities use a shorter grace period for safety."""
        return self._entity_available

    def _update_availability_state(self) -> bool:
        """Update control availability with a shorter grace period."""
        return super()._update_availability_state(
            grace_period=CONTROL_GRACE_PERIOD,
            label=self._control_entity_label,
            clear_optimistic_state=True,
        )


class EveusSensorBase(BaseEveusEntity, SensorEntity):
    """Base sensor entity."""

    def __init__(self, updater: "EveusUpdater", device_number: int = 1) -> None:
        """Initialize the sensor."""
        super().__init__(updater, device_number)
        self._attr_native_value = None
        self._last_valid_value = None
        self._last_error_log = 0.0

    async def async_added_to_hass(self) -> None:
        """Initialize cached sensor state after Home Assistant adds the entity."""
        await super().async_added_to_hass()
        self._update_availability_state()
        self._update_native_value()
        self._update_extra_state_attributes()

    @property
    def available(self) -> bool:
        """Return if the sensor is available with the base grace period."""
        return super().available

    @property
    def native_value(self) -> Any:
        """Return cached sensor value without side effects."""
        if not self.available:
            return None
        return self._attr_native_value

    def _update_native_value(self) -> bool:
        """Refresh sensor value from coordinator data.

        Returns True when the visible value changed.
        """
        previous_value = self._attr_native_value
        if not self.available:
            self._attr_native_value = None
            return previous_value != self._attr_native_value

        try:
            value = self._get_sensor_value()
            if value is not None:
                self._last_valid_value = value
            self._attr_native_value = value
        except Exception as err:
            current_time = time.time()
            if current_time - self._last_error_log > ERROR_LOG_RATE_LIMIT:
                self._last_error_log = current_time
                _LOGGER.debug(
                    "Error getting sensor value for %s: %s",
                    self.unique_id,
                    err,
                    exc_info=True,
                )
            self._attr_native_value = None
        return previous_value != self._attr_native_value

    def _get_sensor_value(self) -> Any:
        """Get sensor value - overridden by subclasses."""
        return self._attr_native_value

    def _update_extra_state_attributes(self) -> bool:
        """Refresh extra attributes. Subclasses may override."""
        return False

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._maybe_finalize_device_info()
        availability_changed = self._update_availability_state()
        value_changed = self._update_native_value()
        attributes_changed = self._update_extra_state_attributes()
        if availability_changed or value_changed or attributes_changed:
            self.async_write_ha_state()


class EveusDiagnosticSensor(EveusSensorBase):
    """Base diagnostic sensor for backward compatibility."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:information"
