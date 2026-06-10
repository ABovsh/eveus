"""Base entity classes for Eveus integration."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Callable, Generic, TypeVar

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import State, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import (
    AVAILABILITY_GRACE_PERIOD,
    CONTROL_GRACE_PERIOD,
    ERROR_LOG_RATE_LIMIT,
)
from .utils import RateLog, get_device_info, get_device_suffix

if TYPE_CHECKING:
    from .common_network import EveusUpdater

_LOGGER = logging.getLogger(__name__)
T = TypeVar("T")


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
        self._availability_log = RateLog()
        self._last_known_available = True
        self._unavailable_since: float | None = None
        self._entity_available = True
        self._grace_recheck_unsub: Callable[[], None] | None = None

        if self.ENTITY_NAME is None:
            raise NotImplementedError("ENTITY_NAME must be defined in child class")

        device_suffix = get_device_suffix(device_number)
        entity_key = slugify(self.ENTITY_NAME)
        self._attr_unique_id = f"eveus{device_suffix}_{entity_key}"
        # Localized display name comes from translations[entity.<platform>.<key>.name].
        # Do not set _attr_name here: Home Assistant gives _attr_name precedence
        # over translation_key and would otherwise keep every entity name English.
        self._attr_translation_key = entity_key
        self._attr_device_info = self._build_device_info()
        self._device_info_finalized = self._device_info_has_firmware()

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self._entity_available

    @property
    def name(self) -> str | None:
        """Return translated HA name, or the English fallback before HA binds a platform."""
        # Use ``platform`` (stable since well before our minimum HA 2025.1) rather
        # than ``platform_data`` (added later, absent on 2025.1) to detect binding.
        # Both flip from None together in add_to_platform_start, so this is an
        # exact equivalent that also runs on the minimum supported HA version.
        if self.platform is None:
            return self.ENTITY_NAME
        return super().name

    @property
    def suggested_object_id(self) -> str | None:
        """Return stable English object id seed regardless of frontend language."""
        return self.ENTITY_NAME

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
            self._cancel_grace_recheck()
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
            self._schedule_grace_recheck(
                grace_period,
                grace_period=grace_period,
                label=label,
                clear_optimistic_state=clear_optimistic_state,
            )
            return previous_available != self._entity_available

        unavailable_duration = current_time - self._unavailable_since
        if unavailable_duration < 0:
            # Wall clock stepped backward since the outage began; re-anchor so a
            # negative age can't keep the grace window open indefinitely.
            self._unavailable_since = current_time
            unavailable_duration = 0.0
        if unavailable_duration < grace_period:
            self._entity_available = True
            self._schedule_grace_recheck(
                grace_period - unavailable_duration,
                grace_period=grace_period,
                label=label,
                clear_optimistic_state=clear_optimistic_state,
            )
            return previous_available != self._entity_available

        self._cancel_grace_recheck()

        if self._last_known_available and self._should_log_availability():
            _LOGGER.debug(
                "%s %s unavailable after grace period (%.0fs)",
                label,
                self.unique_id,
                unavailable_duration,
            )
        self._last_known_available = False
        if clear_optimistic_state:
            clear = getattr(self, "_clear_optimistic_state", None)
            if callable(clear):
                clear()
        self._entity_available = False
        return previous_available != self._entity_available

    def _cancel_grace_recheck(self) -> None:
        """Cancel a scheduled availability grace re-check, if any."""
        if self._grace_recheck_unsub is not None:
            self._grace_recheck_unsub()
            self._grace_recheck_unsub = None

    def _schedule_grace_recheck(
        self,
        delay: float,
        *,
        grace_period: int,
        label: str,
        clear_optimistic_state: bool,
    ) -> None:
        """Re-evaluate availability when the grace window expires.

        Availability is otherwise only recomputed inside coordinator callbacks,
        so with slow (idle/offline) polling a grace period could stretch by up
        to a full poll interval past its configured duration.
        """
        if self.hass is None:
            return
        self._cancel_grace_recheck()

        @callback
        def _recheck(_now) -> None:
            self._grace_recheck_unsub = None
            if self._update_availability_state(
                grace_period=grace_period,
                label=label,
                clear_optimistic_state=clear_optimistic_state,
            ):
                self.async_write_ha_state()

        self._grace_recheck_unsub = async_call_later(self.hass, delay + 0.5, _recheck)

    async def async_will_remove_from_hass(self) -> None:
        """Clean up scheduled callbacks on removal."""
        self._cancel_grace_recheck()
        await super().async_will_remove_from_hass()

    def _should_log_availability(self) -> bool:
        """Rate limit availability logging."""
        return self._availability_log.should_log(ERROR_LOG_RATE_LIMIT)

    def get_cached_data_value(self, key: str, default: Any = None) -> Any:
        """Get a value from the current coordinator payload."""
        data = self._updater.data
        if data is not None:
            value = data.get(key)
            if value is not None:
                return value
        return default

    def _build_device_info(self) -> dict[str, Any]:
        """Build device information from the latest available snapshot."""
        data = self._updater.data if isinstance(self._updater.data, dict) else None
        return get_device_info(
            self._updater.host,
            data or {},
            self._device_number,
            scheme=getattr(self._updater, "scheme", "http"),
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

        Re-runs when already-finalized metadata drifts (a firmware OTA upgrade,
        or a serial number that only appeared in a later payload), so the
        Devices page doesn't show stale values until the next restart.
        """
        if not self._updater.data:
            return

        new_info = self._build_device_info()
        if not new_info.get("sw_version") or new_info["sw_version"] == "Unknown":
            return

        if self._device_info_finalized:
            if new_info == self._attr_device_info:
                # Even with unchanged metadata the registry write must happen
                # once per runtime: it clears the legacy hw_version that older
                # releases stored (Wi-Fi firmware is not a hardware revision).
                if getattr(self._updater, "_device_registry_finalized", False):
                    return
            else:
                # Metadata drifted after finalization: allow one registry refresh.
                self._updater._device_registry_finalized = False

        self._attr_device_info = new_info
        self._device_info_finalized = True

        if self.hass is None:
            return
        if not getattr(self._updater, "_device_registry_finalized", False):
            registry = dr.async_get(self.hass)
            identifiers = new_info.get("identifiers")
            if not identifiers:
                return
            device = registry.async_get_device(identifiers=identifiers)
            if device is None:
                return
            update_kwargs: dict[str, Any] = {
                "sw_version": new_info["sw_version"],
                "model": new_info.get("model"),
                "manufacturer": new_info.get("manufacturer"),
            }
            # hw_version is always cleared: earlier releases wrote the Wi-Fi
            # module firmware there, which is not a hardware revision.
            update_kwargs["hw_version"] = None
            if new_info.get("serial_number"):
                update_kwargs["serial_number"] = new_info["serial_number"]
            registry.async_update_device(device.id, **update_kwargs)
            self._updater._device_registry_finalized = True

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

    def _update_availability_state(self, **_kwargs: Any) -> bool:
        """Update control availability with a shorter grace period.

        Accepts (and overrides) the base keyword arguments so the scheduled
        grace re-check callback can call it polymorphically: control entities
        always use their own shorter grace period and optimistic-state reset.
        """
        return super()._update_availability_state(
            grace_period=CONTROL_GRACE_PERIOD,
            label=self._control_entity_label,
            clear_optimistic_state=True,
        )


class OptimisticControlMixin(Generic[T]):
    """Shared optimistic-state reconciliation for command-capable controls."""

    def _init_optimistic_control(self) -> None:
        """Initialize common optimistic-control state."""
        self._optimistic_value: T | None = None
        self._optimistic_value_time = 0.0
        self._last_device_value: T | None = None
        self._last_successful_read = 0.0
        # Serialize rapid repeated commands on the SAME control. The command
        # manager serializes HTTP at the coordinator level, but the per-entity
        # pending/optimistic bookkeeping runs around that await; without this an
        # older command finishing could briefly publish a stale value over a
        # newer one still in flight (e.g. dragging the Charging Current slider).
        self._command_lock = asyncio.Lock()

    def _clear_optimistic_state(self) -> None:
        """Clear optimistic state when the device is offline."""
        self._optimistic_value = None

    def _set_optimistic_value(self, value: T) -> None:
        """Store an optimistic value after a successful command."""
        self._optimistic_value = value
        self._optimistic_value_time = time.time()

    def _optimistic_value_is_valid(self, current_time: float, ttl: float) -> bool:
        """Return whether the optimistic value should still be trusted.

        Uses a wall-clock delta, so a backward system-clock step makes the age
        negative; treat that as expired (untrustworthy timer) instead of
        "valid forever".
        """
        if self._optimistic_value is None:
            return False
        age = current_time - self._optimistic_value_time
        return 0 <= age < ttl

    def _expire_optimistic_value(self, current_time: float, ttl: float) -> None:
        """Expire optimistic state after its absolute TTL (or a backward clock)."""
        if self._optimistic_value is None:
            return
        age = current_time - self._optimistic_value_time
        if not 0 <= age < ttl:
            self._optimistic_value = None

    def _reconcile_with_device(
        self,
        new_value: T,
        current_time: float,
        confirm_fn: Callable[[T, T], bool],
        *,
        mismatch_ttl: float = 10.0,
    ) -> None:
        """Record a device value and clear optimistic state when reconciled."""
        self._last_device_value = new_value
        self._last_successful_read = current_time

        if self._optimistic_value is None:
            return
        age = current_time - self._optimistic_value_time
        if (
            confirm_fn(self._optimistic_value, new_value)
            or age > mismatch_ttl
            or age < 0
        ):
            self._optimistic_value = None


_UNSET: Any = object()


class WriteOnChangeMixin:
    """Push HA state only when the visible value or availability actually changes.

    Why: DataUpdateCoordinator swaps `updater.data` BEFORE notifying listeners,
    so any "previous = self.<prop>" comparison inside `_handle_coordinator_update`
    reads the *new* data and always matches the current value — masking real
    transitions and emitting redundant state_changed events on every poll.
    The mixin tracks the value we actually pushed to HA last time instead.
    """

    def _init_write_on_change(self) -> None:
        """Initialize change-detection state. Call from __init__."""
        self._last_written_value: Any = _UNSET
        self._last_written_available: bool | None = None

    def _write_if_changed(self, value: Any) -> bool:
        """Push HA state if value or availability changed since last write."""
        available_now = self.available  # type: ignore[attr-defined]
        if (
            value == self._last_written_value
            and available_now == self._last_written_available
        ):
            return False
        self._last_written_value = value
        self._last_written_available = available_now
        self.async_write_ha_state()  # type: ignore[attr-defined]
        return True

    def _write_availability_only(self) -> bool:
        """Push HA state for an availability change without touching the value.

        Used while a command is in flight: the displayed value must stay pinned
        to the optimistic/pending value, but an availability transition (e.g. the
        charger going offline mid-command) must still reach HA.
        """
        available_now = self.available  # type: ignore[attr-defined]
        if available_now == self._last_written_available:
            return False
        self._last_written_available = available_now
        self.async_write_ha_state()  # type: ignore[attr-defined]
        return True


class EveusSensorBase(BaseEveusEntity, SensorEntity):
    """Base sensor entity."""

    def __init__(self, updater: "EveusUpdater", device_number: int = 1) -> None:
        """Initialize the sensor."""
        super().__init__(updater, device_number)
        self._attr_native_value = None
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
