"""Shared lifecycle for command-backed Eveus control entities."""
from __future__ import annotations

import logging
import time
from typing import Any, Generic, TypeVar

from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from .common_base import BaseEveusEntity, OptimisticControlMixin
from .const import OPTIMISTIC_CONTROL_TTL

_LOGGER = logging.getLogger(__name__)

T = TypeVar("T")  # pragma: no mutate - name arg is never introspected (no T.__name__ use)


class CommandBackedEntity(OptimisticControlMixin[T], BaseEveusEntity, Generic[T]):
    """Base class for controls that reconcile command state with device reads."""

    @property
    def _state_key(self) -> str:
        """Return the coordinator payload key backing this control."""
        return self.__dict__["_state_key"]

    @_state_key.setter  # pragma: no mutate - equivalent: both getter/setter bypass the descriptor via self.__dict__["_state_key"] directly, so removing the setter decorator (making it a plain non-data-descriptor method) still round-trips correctly through normal instance-attribute assignment/lookup rules
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

    def _set_pending(self, value: Any) -> None:
        """Store the subclass-specific in-flight command sentinel."""
        raise NotImplementedError

    async def _send_pinned_command(
        self,
        *,
        device_value: Any,
        pending: Any,
        shown: Any,
        accepted: T,
        rejected_message: str,
        failure_prefix: str,
    ) -> None:
        """Send one write while the display is pinned to the requested value.

        The caller holds ``_command_lock``. A write the charger does not accept
        raises ``rejected_message``; any other error except an auth rejection
        becomes a ``failure_prefix`` error. However the write ends — accepted,
        rejected, raised or cancelled — the pin is released and the display
        re-resolved, so an older write can never leave its value behind.
        """
        self._set_pending(pending)
        self._set_display_value(shown)
        self._write_if_changed(shown)  # type: ignore[attr-defined]
        try:
            if not await self._updater.send_command(self._command, device_value):  # type: ignore[attr-defined]
                raise HomeAssistantError(rejected_message)
            self._set_optimistic_value(accepted)
        except (HomeAssistantError, ConfigEntryAuthFailed):
            raise
        except Exception as err:
            _LOGGER.debug("%s: %s", failure_prefix, type(err).__name__)
            raise HomeAssistantError(f"{failure_prefix}: {err}") from err
        finally:
            self._set_pending(None)
            value = self._resolve_display_value()
            self._set_display_value(value)
            self._write_if_changed(value)  # type: ignore[attr-defined]

    @callback  # pragma: no mutate - HA callback-marker decorator, only sets _hass_callback for the runtime scheduler; no test observes it
    def _handle_coordinator_update(self) -> None:
        """Handle updated data and reconcile command state with device state."""
        self._maybe_finalize_device_info()
        self._update_availability_state()
        if self._get_pending() is not None:
            # A command is in flight: keep the displayed value pinned to the
            # optimistic/pending value (don't reconcile against a poll), but still
            # push an availability transition so the control can't show a stale
            # "available" if the charger drops offline mid-command.
            self._write_availability_only()  # type: ignore[attr-defined]
            return

        current_time = time.monotonic()
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
