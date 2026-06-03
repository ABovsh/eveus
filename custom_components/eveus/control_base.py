"""Shared lifecycle for command-backed Eveus control entities."""
from __future__ import annotations

import time
from typing import Any, Generic, TypeVar

from homeassistant.core import callback

from .common_base import BaseEveusEntity, OptimisticControlMixin
from .const import OPTIMISTIC_CONTROL_TTL

T = TypeVar("T")


class CommandBackedEntity(OptimisticControlMixin[T], BaseEveusEntity, Generic[T]):
    """Base class for controls that reconcile command state with device reads."""

    @property
    def _state_key(self) -> str:
        """Return the coordinator payload key backing this control."""
        return self.__dict__["_state_key"]

    @_state_key.setter
    def _state_key(self, value: str) -> None:
        """Store the coordinator payload key backing this control."""
        self.__dict__["_state_key"] = value

    def _read_device_value(self) -> T | None:
        """Return the latest valid device value, or None when unavailable."""
        raise NotImplementedError

    def _values_equal(self, optimistic: T, device: T) -> bool:
        """Return whether a device value confirms an optimistic value."""
        return optimistic == device

    def _resolve_display_value(self) -> Any:
        """Resolve the value that should be exposed to Home Assistant."""
        raise NotImplementedError

    def _set_display_value(self, value: Any) -> None:
        """Store the resolved display value on the entity."""
        raise NotImplementedError

    def _get_pending(self) -> Any:
        """Return the subclass-specific in-flight command sentinel."""
        raise NotImplementedError

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data and reconcile command state with device state."""
        self._maybe_finalize_device_info()
        self._update_availability_state()
        if self._get_pending() is not None:
            return

        current_time = time.time()
        device_value = self._read_device_value()
        if device_value is not None:
            self._reconcile_with_device(
                device_value,
                current_time,
                self._values_equal,
            )

        self._expire_optimistic_value(current_time, OPTIMISTIC_CONTROL_TTL)

        current_value = self._resolve_display_value()
        self._set_display_value(current_value)
        self._write_if_changed(current_value)  # type: ignore[attr-defined]
