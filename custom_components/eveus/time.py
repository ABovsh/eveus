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
from homeassistant.util import dt as ha_dt

from . import EveusConfigEntry
from .common_base import (
    ControlEntityMixin,
    WriteOnChangeMixin,
)
from .control_base import CommandBackedEntity
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
    ControlEntityMixin,
    CommandBackedEntity[int],
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

    def _read_device_value(self) -> int | None:
        """Return the latest valid schedule minutes from coordinator data."""
        if not (
            self._updater.available
            and self._updater.data
            and self._state_key in self._updater.data
        ):
            return None
        device_value = get_safe_value(self._updater.data, self._state_key, int)
        if device_value is not None and 0 <= device_value < 1440:
            return int(device_value)
        return None

    def _values_equal(self, optimistic: int, device: int) -> bool:
        """Return whether device minutes confirm the optimistic value."""
        return optimistic == device

    def _resolve_display_value(self) -> dt.time | None:
        """Resolve the schedule time display value."""
        return minutes_to_time(self._resolve_minutes())

    def _set_display_value(self, value: dt.time | None) -> None:
        """Store the schedule time display value."""
        self._attr_native_value = value

    def _get_pending(self) -> int | None:
        """Return the pending schedule command sentinel."""
        return self._pending_value

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

        async with self._command_lock:
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
        restored = ha_dt.parse_time(state.state)
        if restored is None:
            return
        self._last_device_value = time_to_minutes(restored)
        self._last_successful_read = _time.time()
        self._attr_native_value = restored

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
