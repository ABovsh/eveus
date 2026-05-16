"""Support for Eveus select entities (time zone)."""
from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EveusConfigEntry
from .common_base import BaseEveusEntity, ControlEntityMixin, WriteOnChangeMixin
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
    ControlEntityMixin,
    BaseEveusEntity,
    SelectEntity,
):
    """Time-zone offset reported by and sent to the charger's `timeZone` field."""

    ENTITY_NAME = "Time Zone"
    _attr_icon = "mdi:map-clock-outline"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    _attr_options = list(TIMEZONE_OPTIONS)
    _control_entity_label = "Select"

    def __init__(self, updater, device_number: int = 1) -> None:
        super().__init__(updater, device_number)
        self._init_write_on_change()

    @property
    def current_option(self) -> str | None:
        """Return the formatted timezone string from coordinator data."""
        value = get_safe_value(self._updater.data or {}, "timeZone", int, None)
        if value is None:
            return None
        formatted = _format_tz(value)
        return formatted if formatted in TIMEZONE_OPTIONS else None

    async def async_select_option(self, option: str) -> None:
        """Send `timeZone=<int>` to the charger."""
        if option not in TIMEZONE_OPTIONS:
            raise HomeAssistantError(f"Unsupported time zone: {option}")
        offset = int(option)
        success = await self._updater.send_command("timeZone", offset)
        if not success:
            raise HomeAssistantError(
                f"Eveus charger did not accept timeZone={option}"
            )
        _LOGGER.debug("Time zone changed to %s", option)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Push HA state only when the visible option or availability changes."""
        self._maybe_finalize_device_info()
        self._update_availability_state()
        self._write_if_changed(self.current_option)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EveusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eveus select entities."""
    runtime_data = entry.runtime_data
    async_add_entities(
        [EveusTimeZoneSelect(runtime_data.updater, runtime_data.device_number)]
    )
