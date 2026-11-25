"""Support for Eveus select entities."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData, RestoredExtraData

from . import EveusConfigEntry
from .common_base import (
    BaseEveusEntity,
    ControlEntityMixin,
    OptimisticControlMixin,
    WriteOnChangeMixin,
)
from .control_base import command_failure
from .const import (
    CONF_FOLLOW_HA_TIME_ZONE,
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
_LEGACY_ADAPTIVE_OPTIONS = {**ADAPTIVE_OPTIONS, 4: "Auto 2"}
_LEGACY_ADAPTIVE_TO_DEVICE = {option: value for value, option in _LEGACY_ADAPTIVE_OPTIONS.items()}


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

    @property
    def _legacy_firmware(self) -> bool:
        return getattr(self._updater, "is_legacy_firmware", False) is True

    def _source_keys(self) -> tuple[str, ...]:
        return (self.READ_KEY,)

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
        await self._async_send_option(option)

    def _accept_option(self, value: int) -> None:
        self._set_optimistic_value(value)

    async def _async_send_option(
        self, option: str, *, preflight: Callable[[], bool] | None = None,
    ) -> None:
        """Send ``option``; ``preflight`` may veto it right before the wire."""
        if option not in self.OPTION_TO_DEVICE:
            raise HomeAssistantError(f"Unsupported {self.ENTITY_NAME}: {option}")
        value = self.OPTION_TO_DEVICE[option]
        async with self._command_lock:
            optimistic = not self.assumed_state
            if optimistic:
                self._set_optimistic_value(value)
            self._command_pending = True
            if optimistic:
                self._write_if_changed(option)
            try:
                kwargs = {"preflight": preflight} if preflight is not None else {}
                success = await self._updater.send_command(self.WRITE_KEY, value, **kwargs)
            except Exception:
                self._optimistic_value = None
                if optimistic:
                    self._write_if_changed(self.current_option)
                raise
            finally:
                self._command_pending = False
            if not success:
                self._optimistic_value = None
                if optimistic:
                    self._write_if_changed(self.current_option)
                raise command_failure(
                    self._updater, f"Eveus charger did not accept {self.WRITE_KEY}={value}"
                )
            self._accept_option(value)
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
    """Time-zone offset reported by and sent to the charger's `timeZone` field.

    While the Follow HA Time Zone switch is on the integration owns the offset,
    so a manual choice is refused (it would be undone within a few polls); the
    select keeps showing what the charger holds.
    """

    ENTITY_NAME = "Time Zone"
    READ_KEY = "timeZone"
    WRITE_KEY = "timeZone"
    DEVICE_TO_OPTION = {i: _format_tz(i) for i in range(-12, 15)}
    OPTION_TO_DEVICE = {option: offset for offset, option in DEVICE_TO_OPTION.items()}
    _attr_icon = "mdi:map-clock-outline"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(TIMEZONE_OPTIONS)

    def __init__(self, updater, device_number: int = 1, entry=None) -> None:
        super().__init__(updater, device_number)
        self._entry = entry

    def _following(self) -> bool:
        return self._entry is not None and bool(self._entry.data.get(CONF_FOLLOW_HA_TIME_ZONE))

    async def async_select_option(self, option: str) -> None:
        refusal = "Follow HA Time Zone is on: turn it off to set the Time Zone by hand"
        if self._following():
            raise HomeAssistantError(refusal)
        try:
            await self._async_send_option(
                option,
                preflight=(lambda: not self._following()) if self._entry is not None else None,
            )
        except HomeAssistantError as err:
            # Following started while this choice waited its turn: nothing was
            # sent, so the reason is the switch, not the charger.
            if self._following():
                raise HomeAssistantError(refusal) from err
            raise


class EveusAdaptiveModeSelect(_EveusIntegerSelect):
    """Adaptive charging mode read from `aiStatus` and written to `aiMode`."""

    ENTITY_NAME = "Adaptive Mode"
    READ_KEY = "aiStatus"
    WRITE_KEY = "aiMode"
    _attr_icon = "mdi:auto-mode"
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def DEVICE_TO_OPTION(self) -> dict[int, str]:
        return _LEGACY_ADAPTIVE_OPTIONS if self._legacy_firmware else ADAPTIVE_OPTIONS

    @property
    def OPTION_TO_DEVICE(self) -> dict[str, int]:
        return _LEGACY_ADAPTIVE_TO_DEVICE if self._legacy_firmware else ADAPTIVE_TO_DEVICE

    @property
    def options(self) -> list[str]:
        return list(self.DEVICE_TO_OPTION.values())

    def _write_if_changed(self, value: str | None) -> bool:
        # Firmware can be confirmed after the select is added to HA.
        if self._legacy_firmware:
            return super()._write_if_changed((value, True))  # pragma: no mutate - any marker that differs from the plain value republishes once when the options grow; True and False are the same here
        return super()._write_if_changed(value)


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

    def __init__(self, updater, device_number: int = 1) -> None:
        super().__init__(updater, device_number)
        self._last_legacy_command: int | None = None

    @property
    def assumed_state(self) -> bool:
        """The 1.x web page can set a value that /main omits."""
        return self._legacy_firmware and not self._updater.snapshot.has(self.READ_KEY)

    @property
    def current_option(self) -> str | None:
        if self.assumed_state:
            return self.DEVICE_TO_OPTION.get(self._last_legacy_command)
        return super().current_option

    def _write_if_changed(self, value: str | None) -> bool:
        if self._legacy_firmware:
            return super()._write_if_changed((value, self.assumed_state))
        return super()._write_if_changed(value)

    def _accept_option(self, value: int) -> None:
        if self._legacy_firmware:
            self._last_legacy_command = value
        if self.assumed_state:
            self._write_if_changed(self.current_option)
        else:
            super()._accept_option(value)

    async def _async_restore_state(self, state: State) -> None:
        # Firmware identification can follow restore when the charger starts offline.
        extra = await self.async_get_last_extra_data()
        value = extra.as_dict().get("last_legacy_command") if extra else None
        if type(value) is int and value in self.DEVICE_TO_OPTION:
            self._last_legacy_command = value
        await super()._async_restore_state(state)

    @property
    def extra_restore_state_data(self) -> ExtraStoredData | None:
        """Persist the accepted option even if the charger goes offline."""
        if self._last_legacy_command is None or (
            self._updater.available and not self._legacy_firmware
        ):
            return None
        return RestoredExtraData({"last_legacy_command": self._last_legacy_command})


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EveusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eveus select entities."""
    runtime_data = entry.runtime_data
    entities = [
        EveusTimeZoneSelect(
            runtime_data.updater, runtime_data.device_number, entry
        ),
        EveusAdaptiveModeSelect(runtime_data.updater, runtime_data.device_number),
    ]
    if entry.data.get(CONF_MODEL):
        entities.append(
            EveusMinVoltageSelect(runtime_data.updater, runtime_data.device_number)
        )
    async_add_entities(entities)
