"""Shared lifecycle for command-backed Eveus control entities."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, Generic, TypeVar

from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from .common_base import BaseEveusEntity, OptimisticControlMixin
from .common_command import REVE_NOT_ANSWERING
from .const import OPTIMISTIC_CONTROL_TTL

_LOGGER = logging.getLogger(__name__)

# REVE's GraphQL server answers HTTP 500 for up to ~18 minutes after the
# charger reconnects to Wi-Fi: nothing was refused, so say what happened.
REVE_NOT_ANSWERING_MESSAGE = (
    "The charger's REVE interface is not answering; try again in a few minutes"
)


def command_failure(updater: Any, rejected_message: str) -> HomeAssistantError:
    """The error for a command ``send_command`` reported failed: REVE's
    interface not answering when that is why, else ``rejected_message``."""
    if getattr(updater, "last_command_failure", None) == REVE_NOT_ANSWERING:
        return HomeAssistantError(REVE_NOT_ANSWERING_MESSAGE)
    return HomeAssistantError(rejected_message)

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

    def _source_keys(self) -> tuple[str, ...]:
        """A control is backed by exactly the field it reads back."""
        return (self._state_key,)

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
        extra: dict[str, Any] | None = None,
        preflight: Callable[[], bool] | None = None,
    ) -> None:
        """Send one write while the display is pinned to the requested value.

        The caller holds ``_command_lock``. A write the charger does not accept
        raises ``rejected_message``; any other error except an auth rejection
        becomes a ``failure_prefix`` error. However the write ends — accepted,
        rejected, raised or cancelled — the pin is released and the display
        re-resolved, so an older write can never leave its value behind.
        ``extra`` rides along as sibling form fields (e.g. OCPP's `ocppVendor`).
        """
        # A write the charger would ignore or drop is refused with its reason
        # before anything is shown or sent (REVE's session limits).
        refusal = getattr(self._updater, "command_refusal", None)
        reason = refusal(self._command) if callable(refusal) else None
        if isinstance(reason, str):
            raise HomeAssistantError(reason)
        self._set_pending(pending)
        self._set_display_value(shown)
        self._write_if_changed(shown)  # type: ignore[attr-defined]
        superseded = False
        try:
            kwargs = {}
            if extra is not None:
                kwargs["extra"] = extra
            if preflight is not None:
                kwargs["preflight"] = preflight
            success = await self._updater.send_command(  # type: ignore[attr-defined]
                self._command, device_value, **kwargs
            )
            # An absolute setpoint superseded while waiting (or retrying)
            # is a successful coalescing decision, not a rejected command.
            if preflight is not None and not preflight():
                superseded = True
                return
            if not success:
                raise command_failure(self._updater, rejected_message)
            self._set_optimistic_value(accepted)
        except (HomeAssistantError, ConfigEntryAuthFailed):
            raise
        except Exception as err:
            _LOGGER.debug("%s: %s", failure_prefix, type(err).__name__)
            raise HomeAssistantError(f"{failure_prefix}: {err}") from err
        finally:
            self._set_pending(None)
            # The superseding write is queued behind the caller's lock and
            # republishes the display itself; resolving here would show the
            # charger's old value (a spurious row) between the two requests.
            # If it never arrives, the next poll reconciles the display.
            if not superseded:
                value = self._resolve_display_value()
                self._set_display_value(value)
                self._write_if_changed(value)  # type: ignore[attr-defined]

    @callback
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
