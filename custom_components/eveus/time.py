"""Support for Eveus time entities (schedule start/stop windows)."""
from __future__ import annotations

import datetime as dt
import logging
import time as _time
from dataclasses import dataclass

from homeassistant.components.time import TimeEntity, TimeEntityDescription
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


@dataclass(frozen=True)
class EveusTimeEntityDescription(TimeEntityDescription):
    """Description for an Eveus schedule time entity."""

    command: str = ""
    state_key: str = ""


TIME_DESCRIPTIONS: tuple[EveusTimeEntityDescription, ...] = (
    EveusTimeEntityDescription(
        key="schedule_1_start",
        name="Schedule 1 Start",
        icon="mdi:clock-start",
        entity_category=EntityCategory.CONFIG,
        command="sh1Start",
        state_key="sh1Start",
    ),
    EveusTimeEntityDescription(
        key="schedule_1_stop",
        name="Schedule 1 Stop",
        icon="mdi:clock-end",
        entity_category=EntityCategory.CONFIG,
        command="sh1Stop",
        state_key="sh1Stop",
    ),
    EveusTimeEntityDescription(
        key="schedule_2_start",
        name="Schedule 2 Start",
        icon="mdi:clock-start",
        entity_category=EntityCategory.CONFIG,
        command="sh2Start",
        state_key="sh2Start",
    ),
    EveusTimeEntityDescription(
        key="schedule_2_stop",
        name="Schedule 2 Stop",
        icon="mdi:clock-end",
        entity_category=EntityCategory.CONFIG,
        command="sh2Stop",
        state_key="sh2Stop",
    ),
)


def minutes_to_time(minutes: int | None) -> dt.time | None:
    """Decode a 0..1439 minutes-since-midnight value into datetime.time."""
    if minutes is None:
        return None
    try:
        m = int(minutes)
    except (TypeError, ValueError):
        return None
    if not 0 <= m < 1440:
        return None
    return dt.time(hour=m // 60, minute=m % 60)


def time_to_minutes(value: dt.time) -> int:
    """Encode datetime.time as minutes since midnight (seconds discarded)."""
    return value.hour * 60 + value.minute


class EveusScheduleTimeEntity(
    WriteOnChangeMixin,
    OptimisticControlMixin[int],
    ControlEntityMixin,
    BaseEveusEntity,
    TimeEntity,
):
    """A writable schedule time field (start or stop) backed by the charger."""

    _control_entity_label = "Time"

    def __init__(
        self,
        updater,
        entity_description: EveusTimeEntityDescription,
        device_number: int = 1,
    ) -> None:
        """Initialize the time entity."""
        self.entity_description = entity_description
        self.ENTITY_NAME = entity_description.name
        super().__init__(updater, device_number)
        self._command = entity_description.command
        self._state_key = entity_description.state_key
        self._pending_value: int | None = None
        self._init_optimistic_control()
        self._init_write_on_change()
        self._attr_native_value: dt.time | None = None

    async def async_added_to_hass(self) -> None:
        """Resolve the initial value once coordinator data is available."""
        await super().async_added_to_hass()
        self._attr_native_value = minutes_to_time(self._resolve_minutes())

    @property
    def native_value(self) -> dt.time | None:
        """Return cached time without side effects."""
        return self._attr_native_value

    def _resolve_minutes(self) -> int | None:
        """Resolve minutes value from optimistic, device, or restore state."""
        current_time = _time.time()

        if self._optimistic_value_is_valid(current_time, OPTIMISTIC_CONTROL_TTL):
            return self._optimistic_value

        if (
            self._updater.available
            and self._updater.data
            and self._state_key in self._updater.data
        ):
            device_value = get_safe_value(self._updater.data, self._state_key, int)
            if device_value is not None and 0 <= device_value < 1440:
                return int(device_value)

        if self._last_device_value is not None:
            if current_time - self._last_successful_read < CONTROL_GRACE_PERIOD:
                return self._last_device_value

        return None

    async def async_set_value(self, value: dt.time) -> None:
        """Send the new start/stop value to the charger with optimistic UI."""
        minutes = time_to_minutes(value)

        self._pending_value = minutes
        self._attr_native_value = dt.time(hour=minutes // 60, minute=minutes % 60)
        self._write_if_changed(self._attr_native_value)

        try:
            success = await self._updater.send_command(self._command, minutes)
            if success:
                self._set_optimistic_value(minutes)
            else:
                raise HomeAssistantError(
                    f"Eveus charger did not accept '{self.name}' = "
                    f"{self._attr_native_value.strftime('%H:%M')}"
                )
        finally:
            self._pending_value = None
            self._last_command_time = _time.time()
            self._attr_native_value = minutes_to_time(self._resolve_minutes())
            self._write_if_changed(self._attr_native_value)

    async def _async_restore_state(self, state: State) -> None:
        """Restore previous display value only — no commands sent on startup."""
        if not state or state.state in (None, "unknown", "unavailable"):
            return
        try:
            hh, mm = state.state.split(":", 2)[:2]
            restored = dt.time(hour=int(hh), minute=int(mm))
        except (ValueError, AttributeError):
            return
        self._last_device_value = time_to_minutes(restored)
        self._attr_native_value = restored

    @callback
    def _handle_coordinator_update(self) -> None:
        """Reconcile cached value with the latest coordinator payload."""
        self._maybe_finalize_device_info()
        self._update_availability_state()
        if self._pending_value is not None:
            return

        current_time = _time.time()
        if (
            self._updater.available
            and self._updater.data
            and self._state_key in self._updater.data
        ):
            device_value = get_safe_value(self._updater.data, self._state_key, int)
            if device_value is not None and 0 <= device_value < 1440:
                self._reconcile_with_device(
                    int(device_value),
                    current_time,
                    lambda optimistic, device: optimistic == device,
                )

        self._expire_optimistic_value(current_time, OPTIMISTIC_CONTROL_TTL)

        new_value = minutes_to_time(self._resolve_minutes())
        self._attr_native_value = new_value
        self._write_if_changed(new_value)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EveusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eveus time entities."""
    runtime_data = entry.runtime_data
    updater = runtime_data.updater
    device_number = runtime_data.device_number

    async_add_entities(
        EveusScheduleTimeEntity(updater, description, device_number)
        for description in TIME_DESCRIPTIONS
    )
