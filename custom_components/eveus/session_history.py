"""Sensors that keep the summary of the most recent finished charging session.

Values are captured from the coordinator's charging-finished bus event. The
firmware keeps its session counters until the NEXT session starts (reset happens
at session start, not at charge end), but that still means the numbers are gone
the moment a new session begins — capturing them at the finished event keeps
them permanently. RestoreEntity keeps them across HA restarts.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Optional

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.core import Event, callback
from homeassistant.util import dt as dt_util

from .common_base import EveusSensorBase
from .const import (
    EVENT_CHARGING_FINISHED,
    MAX_COST_VALUE,
    MAX_ENERGY_KWH,
    MAX_SESSION_TIME_SECONDS,
)
from .sensor_definitions import ICON_CURRENCY_UAH, UNIT_UAH
from homeassistant.const import UnitOfEnergy, UnitOfTime

_LOGGER = logging.getLogger(__name__)


class _LastSessionSensorBase(EveusSensorBase):
    """Event-driven sensor; ignores coordinator data for its value."""

    _event_field: str = ""
    # Same sanity ceiling as the live sensors reading this field: a captured or
    # restored value outside [0, _max_value] is corrupt and must not latch into
    # this sensor's persistent state.
    _max_value: float = 0.0

    def __init__(self, updater, device_number: int = 1) -> None:
        super().__init__(updater, device_number)
        self._attr_extra_state_attributes = {}

    @property
    def available(self) -> bool:
        """Historical data — meaningful even while the charger is offline."""
        return True

    @property
    def native_value(self) -> Any:
        return self._attr_native_value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._attr_extra_state_attributes

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(
                EVENT_CHARGING_FINISHED, self._handle_finished_event
            )
        )

    async def _async_restore_state(self, state) -> None:
        await super()._async_restore_state(state)
        try:
            restored = float(state.state)
        except (TypeError, ValueError):
            return
        # float("inf")/"nan" parse without raising; bound like the event path so
        # a corrupt stored state can't resurrect an out-of-domain value.
        if not math.isfinite(restored) or not 0 <= restored <= self._max_value:
            return
        self._attr_native_value = restored
        for attr in ("reason", "finished_at"):
            if attr in state.attributes:
                self._attr_extra_state_attributes[attr] = state.attributes[attr]

    @callback
    def _handle_finished_event(self, event: Event) -> None:
        if event.data.get("device_number") != self._device_number:
            return
        value = self._value_from_event(event.data)
        if value is None:
            self._attr_native_value = None
        else:
            self._attr_native_value = value
            self._attr_extra_state_attributes = {
                "reason": event.data.get("reason"),
                "finished_at": dt_util.now().isoformat(),
            }
        if self.hass is not None:
            self.async_write_ha_state()

    def _value_from_event(self, data: dict[str, Any]) -> Optional[float]:
        # The coordinator already bounds what it fires, but the bus event is
        # public — anything can fire it — so the domain check is repeated here.
        value = data.get(self._event_field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if not math.isfinite(value) or not 0 <= value <= self._max_value:
            return None
        return value


class LastSessionEnergySensor(_LastSessionSensorBase):
    ENTITY_NAME = "Last Session Energy"
    _event_field = "session_energy_kwh"
    _max_value = MAX_ENERGY_KWH
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:battery-charging-100"


class LastSessionCostSensor(_LastSessionSensorBase):
    ENTITY_NAME = "Last Session Cost"
    _event_field = "session_cost"
    _max_value = MAX_COST_VALUE
    _attr_native_unit_of_measurement = UNIT_UAH
    _attr_suggested_display_precision = 2
    _attr_icon = ICON_CURRENCY_UAH


class LastSessionDurationSensor(_LastSessionSensorBase):
    ENTITY_NAME = "Last Session Duration"
    _event_field = "session_duration_s"
    _max_value = MAX_SESSION_TIME_SECONDS
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_icon = "mdi:timer-outline"


def create_last_session_sensors(
    updater, device_number: int
) -> list[_LastSessionSensorBase]:
    """Build the Last Session sensor set."""
    return [
        LastSessionEnergySensor(updater, device_number),
        LastSessionCostSensor(updater, device_number),
        LastSessionDurationSensor(updater, device_number),
    ]
