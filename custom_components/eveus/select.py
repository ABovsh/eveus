"""Support for Eveus select entities (time zone)."""
from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EveusConfigEntry
from .common_base import (
    BaseEveusEntity,
    ControlEntityMixin,
    OptimisticControlMixin,
    WriteOnChangeMixin,
)
from .const import CONTROL_GRACE_PERIOD, OPTIMISTIC_CONTROL_TTL
from .utils import get_safe_value

_LOGGER = logging.getLogger(__name__)


def _format_tz(offset: int) -> str:
    """Render an integer offset as a signed string (`0`, `+3`, `-5`)."""
    if offset == 0:
        return "0"
    return f"{offset:+d}"


# Firmware accepts the full IANA range -12..+14 (verified on R3.05.2).
TIMEZONE_OPTIONS: tuple[str, ...] = tuple(_format_tz(i) for i in range(-12, 15))


class EveusTimeZoneSelect(
    WriteOnChangeMixin,
    OptimisticControlMixin[int],
    ControlEntityMixin,
    BaseEveusEntity,
    SelectEntity,
):
    """Time-zone offset reported by and sent to the charger's `timeZone` field."""

    ENTITY_NAME = "Time Zone"
    _attr_icon = "mdi:map-clock-outline"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(TIMEZONE_OPTIONS)
    _control_entity_label = "Select"

    def __init__(self, updater, device_number: int = 1) -> None:
        super().__init__(updater, device_number)
        self._init_optimistic_control()
        self._init_write_on_change()
        self._command_pending = False

    def _device_option(self) -> str | None:
        """Resolve the formatted timezone string from fresh coordinator data.

        Gated on availability like the other controls: the coordinator retains
        the last payload after failed polls, so without this gate an offline
        charger would reconcile against a stale `timeZone` and revert the choice.
        """
        if not self._updater.available:
            return None
        value = get_safe_value(self._updater.data or {}, "timeZone", int, None)
        if value is None:
            return None
        formatted = _format_tz(value)
        return formatted if formatted in TIMEZONE_OPTIONS else None

    @property
    def current_option(self) -> str | None:
        """Return optimistic value while pending; else device value; else the
        last good value through the grace window (restored across restarts)."""
        if self._optimistic_value_is_valid(time.time(), OPTIMISTIC_CONTROL_TTL):
            return _format_tz(self._optimistic_value)
        device = self._device_option()
        if device is not None:
            return device
        if (
            self._last_device_value is not None
            and 0 <= time.time() - self._last_successful_read < CONTROL_GRACE_PERIOD
        ):
            return _format_tz(self._last_device_value)
        return None

    async def _async_restore_state(self, state: State) -> None:
        """Seed the last device value from the restored HA state.

        Lets the select show its previous offset through the grace window after a
        restart while the charger is still offline, instead of dropping to
        `unknown` until the first successful poll.
        """
        if state is None or state.state in (None, "unknown", "unavailable"):
            return
        if state.state in TIMEZONE_OPTIONS:
            try:
                self._last_device_value = int(state.state)
                self._last_successful_read = time.time()
            except (TypeError, ValueError):
                pass

    async def async_select_option(self, option: str) -> None:
        """Send `timeZone=<int>` to the charger with optimistic UI."""
        if option not in TIMEZONE_OPTIONS:
            raise HomeAssistantError(f"Unsupported time zone: {option}")
        offset = int(option)
        async with self._command_lock:
            self._set_optimistic_value(offset)
            # Suppress reconciliation while the command is in flight: the charger
            # can take longer than the optimistic mismatch TTL to reflect the new
            # zone, so a routine poll mid-command must not expire our value.
            self._command_pending = True
            self._write_if_changed(option)
            try:
                success = await self._updater.send_command("timeZone", offset)
            except Exception:
                self._optimistic_value = None
                self._write_if_changed(self.current_option)
                raise
            finally:
                self._command_pending = False
            if not success:
                self._optimistic_value = None
                self._write_if_changed(self.current_option)
                raise HomeAssistantError(
                    f"Eveus charger did not accept timeZone={option}"
                )
            # Re-stamp optimistic on success so a stale poll arriving right after
            # the command can't immediately expire it before the charger reports
            # the new zone.
            self._set_optimistic_value(offset)
        _LOGGER.debug("Time zone changed to %s", option)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Push HA state only when the visible option or availability changes."""
        self._maybe_finalize_device_info()
        self._update_availability_state()
        if self._command_pending:
            # Mirror the other controls: don't reconcile against device data
            # while our own command is still in flight.
            self._write_if_changed(self.current_option)
            return
        current_time = time.time()
        device_option = self._device_option()
        if device_option is not None:
            try:
                device_value = int(device_option)
            except ValueError:
                device_value = None
            if device_value is not None:
                self._reconcile_with_device(
                    device_value,
                    current_time,
                    lambda optimistic, device: optimistic == device,
                )
        self._expire_optimistic_value(current_time, OPTIMISTIC_CONTROL_TTL)
        self._write_if_changed(self.current_option)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EveusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eveus select entities."""
    runtime_data = entry.runtime_data
    async_add_entities(
        [EveusTimeZoneSelect(runtime_data.updater, runtime_data.device_number)]
    )
