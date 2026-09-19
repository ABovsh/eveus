"""Support for Eveus select entities."""
from __future__ import annotations

import logging
import time

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
from .const import (
    CONF_MODEL,
    MIN_VOLTAGE_OPTIONS,
    OPTIMISTIC_CONTROL_TTL,
    UNUSABLE_RESTORED_STATES,
)

_LOGGER = logging.getLogger(__name__)


def _format_tz(offset: int) -> str:
    """Render an integer offset as a signed string (`0`, `+3`, `-5`)."""
    if offset == 0:
        return "0"
    return f"{offset:+d}"


# Firmware accepts the full IANA range -12..+14 (verified on R3.05.2).
TIMEZONE_OPTIONS: tuple[str, ...] = tuple(_format_tz(i) for i in range(-12, 15))
ADAPTIVE_OPTIONS = {0: "Off", 1: "Voltage", 2: "Auto", 3: "Power"}
ADAPTIVE_TO_DEVICE = {option: value for value, option in ADAPTIVE_OPTIONS.items()}


class _EveusIntegerSelect(
    WriteOnChangeMixin,
    OptimisticControlMixin[int],
    ControlEntityMixin,
    BaseEveusEntity,
    SelectEntity,
):
    """Base for selects backed by an integer charger setting."""

    READ_KEY: str
    WRITE_KEY: str
    DEVICE_TO_OPTION: dict[int, str]
    OPTION_TO_DEVICE: dict[str, int]
    _control_entity_label = "Select"

    def __init__(self, updater, device_number: int = 1) -> None:
        super().__init__(updater, device_number)
        self._init_optimistic_control()
        self._init_write_on_change()
        self._command_pending = False

    def _device_value(self) -> int | None:
        """The charger's setting, only when it maps to an offered option."""
        if not self._updater.available:
            return None
        value = self._updater.snapshot.get_int(self.READ_KEY)
        return value if value in self.DEVICE_TO_OPTION else None

    @property
    def current_option(self) -> str | None:
        """Return optimistic, device, or grace-window restored option."""
        value = self._resolve_held_value(self._device_value())
        return None if value is None else self.DEVICE_TO_OPTION.get(value)

    async def _async_restore_state(self, state: State) -> None:
        """Seed the last device value from the restored HA state."""
        if state is None or state.state in UNUSABLE_RESTORED_STATES:
            return
        value = self.OPTION_TO_DEVICE.get(state.state)
        if value is not None:
            self._last_device_value = value
            self._last_successful_read = time.monotonic()

    async def async_select_option(self, option: str) -> None:
        """Send the selected integer value to the charger with optimistic UI."""
        if option not in self.OPTION_TO_DEVICE:
            raise HomeAssistantError(f"Unsupported {self.ENTITY_NAME}: {option}")
        value = self.OPTION_TO_DEVICE[option]
        async with self._command_lock:
            self._set_optimistic_value(value)
            self._command_pending = True
            self._write_if_changed(option)
            try:
                success = await self._updater.send_command(self.WRITE_KEY, value)
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
                    f"Eveus charger did not accept {self.WRITE_KEY}={value}"
                )
            self._set_optimistic_value(value)
        _LOGGER.debug("%s changed to %s", self.ENTITY_NAME, option)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Push HA state only when the visible option or availability changes."""
        self._maybe_finalize_device_info()
        self._update_availability_state()
        if self._command_pending:
            self._write_if_changed(self.current_option)
            return
        current_time = time.monotonic()
        device_value = self._device_value()
        if device_value is not None:
            self._reconcile_with_device(
                device_value,
                current_time,
                lambda optimistic, device: optimistic == device,
            )
        self._expire_optimistic_value(current_time, OPTIMISTIC_CONTROL_TTL)
        self._write_if_changed(self.current_option)


class EveusTimeZoneSelect(_EveusIntegerSelect):
    """Time-zone offset reported by and sent to the charger's `timeZone` field."""

    ENTITY_NAME = "Time Zone"
    READ_KEY = "timeZone"
    WRITE_KEY = "timeZone"
    DEVICE_TO_OPTION = {i: _format_tz(i) for i in range(-12, 15)}
    OPTION_TO_DEVICE = {option: offset for offset, option in DEVICE_TO_OPTION.items()}
    _attr_icon = "mdi:map-clock-outline"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(TIMEZONE_OPTIONS)


class EveusAdaptiveModeSelect(_EveusIntegerSelect):
    """Adaptive charging mode read from `aiStatus` and written to `aiMode`."""

    ENTITY_NAME = "Adaptive Mode"
    READ_KEY = "aiStatus"
    WRITE_KEY = "aiMode"
    DEVICE_TO_OPTION = ADAPTIVE_OPTIONS
    OPTION_TO_DEVICE = ADAPTIVE_TO_DEVICE
    _attr_icon = "mdi:auto-mode"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = ["Off", "Voltage", "Auto", "Power"]


class EveusMinVoltageSelect(_EveusIntegerSelect):
    """Minimum charger voltage read from and written to `minVoltage`."""

    ENTITY_NAME = "Minimum voltage"
    READ_KEY = "minVoltage"
    WRITE_KEY = "minVoltage"
    DEVICE_TO_OPTION = {int(option): option for option in MIN_VOLTAGE_OPTIONS}
    OPTION_TO_DEVICE = {option: int(option) for option in MIN_VOLTAGE_OPTIONS}
    _attr_icon = "mdi:sine-wave"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = MIN_VOLTAGE_OPTIONS


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EveusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eveus select entities."""
    runtime_data = entry.runtime_data
    entities = [
        EveusTimeZoneSelect(runtime_data.updater, runtime_data.device_number),
        EveusAdaptiveModeSelect(runtime_data.updater, runtime_data.device_number),
    ]
    if entry.data.get(CONF_MODEL):
        entities.append(
            EveusMinVoltageSelect(runtime_data.updater, runtime_data.device_number)
        )
    async_add_entities(entities)
