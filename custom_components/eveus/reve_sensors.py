"""REVE station memory and start time, for troubleshooting the firmware.

REVE_PLUS 1.0.3 reports its free memory and uptime. The Wi-Fi module restarts
when its memory fragments, so these show how close it runs to that. They are
created only for REVE and disabled by default: nobody needs them to charge.
No ``state_class``: a fault is read off recent history, and long-term
statistics would cost rows forever. Memory is published in whole KiB, so the
byte-level churn of every poll does not reach the recorder, and Free Memory,
which wanders by a KiB between polls, holds until it has moved by two (463 rows
a day for 10 distinct values before); the start time moves only when the
station restarts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory, UnitOfInformation

from .common_base import EveusSensorBase

_FREE_MEMORY_KEY = "heap_free"
# KiB. The minimum only falls and the largest block moves in real steps of 2-4
# KiB, so only the free figure, which dithers, is held.
_FREE_MEMORY_DEADBAND = 2

# (name, key in the updater's reve_station_system, icon)
_MEMORY: tuple[tuple[str, str, str], ...] = (
    ("REVE Free Memory", _FREE_MEMORY_KEY, "mdi:memory"),
    ("REVE Min Free Memory", "heap_min_free", "mdi:memory-arrow-down"),
    ("REVE Largest Free Block", "heap_largest_free_block", "mdi:view-grid-outline"),
)


_STARTED_NAME = "REVE Started"
# Every name these sensors take, for the translation check.
STATION_SENSOR_NAMES: tuple[str, ...] = (*(name for name, _key, _icon in _MEMORY), _STARTED_NAME)


class _ReveStationSensor(EveusSensorBase):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, updater, name: str, key: str, icon: str, device_number: int = 1) -> None:
        self.ENTITY_NAME = name
        self._key = key
        self._attr_icon = icon
        super().__init__(updater, device_number)

    def _reading(self) -> Any:
        return self._updater.reve_station_system.get(self._key)

    @property
    def available(self) -> bool:
        """Unavailable while the firmware does not report it, or a ``/main``
        poll left no fresh figure."""
        return super().available and self._reading() is not None


class ReveMemorySensor(_ReveStationSensor):
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.KIBIBYTES
    _attr_suggested_display_precision = 0

    def _get_sensor_value(self) -> int | None:
        value = self._reading()
        return None if value is None else round(value / 1024)


class ReveFreeMemorySensor(ReveMemorySensor):
    _deadband = _FREE_MEMORY_DEADBAND


class ReveStartedSensor(_ReveStationSensor):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, updater, device_number: int = 1) -> None:
        super().__init__(updater, _STARTED_NAME, "started_at", "mdi:restart", device_number)

    def _get_sensor_value(self) -> datetime | None:
        value = self._reading()
        return None if value is None else datetime.fromtimestamp(value, tz=timezone.utc)


def create_reve_station_sensors(updater, device_number: int = 1) -> list[_ReveStationSensor]:
    return [
        *(
            (ReveFreeMemorySensor if key == _FREE_MEMORY_KEY else ReveMemorySensor)(
                updater, name, key, icon, device_number
            )
            for name, key, icon in _MEMORY
        ),
        ReveStartedSensor(updater, device_number),
    ]
