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

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory, UnitOfElectricPotential, UnitOfInformation
from homeassistant.core import Event, callback

from .common_base import EveusSensorBase
from .const import MAX_VOLTAGE_V
from .session_history import _LastSessionSensorBase

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
STATION_SENSOR_NAMES: tuple[str, ...] = (
    *(name for name, _key, _icon in _MEMORY),
    _STARTED_NAME,
    "Last Session Min Voltage",
    "Last Session Max Voltage",
)


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


# Seconds to wait before each read of the station's session record. After a
# finish the station may not have stored the session yet; a read that finds
# only an older one is tried again.
_AFTER_FINISH_DELAYS: tuple[float, ...] = (10.0, 30.0, 60.0)
_AT_START_DELAYS: tuple[float, ...] = (0.0, 30.0, 60.0)
# A session counts as the one that just ended when it stopped this recently.
_JUST_ENDED_SECONDS = 120


class _ReveLastSessionVoltageSensor(_LastSessionSensorBase):
    """Lowest or highest mains voltage of the last finished session, as the
    station recorded it (phase A; REVE is one-phase).

    Read from the station when this entry starts and after each finish, never
    polled: the record is written once a session ends, so the sensor changes
    once per session and keeps no long-term statistics. The station keeps it,
    so a session that ended while Home Assistant was down is still read.
    """

    _max_value = float(MAX_VOLTAGE_V)
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_suggested_display_precision = 0
    _key = ""

    def __init__(self, updater, name: str, key: str, icon: str, device_number: int = 1) -> None:
        self.ENTITY_NAME = name
        self._key = key
        self._attr_icon = icon
        self._read_task: asyncio.Task[None] | None = None
        super().__init__(updater, device_number)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel_reads)
        self._start_reads(_AT_START_DELAYS, None)

    @callback
    def _handle_finished_event(self, event: Event) -> None:
        if event.data.get("device_number") != self._device_number:
            return
        self._start_reads(_AFTER_FINISH_DELAYS, time.time() - _JUST_ENDED_SECONDS)

    @callback
    def _cancel_reads(self) -> None:
        if self._read_task is not None:
            self._read_task.cancel()
            self._read_task = None

    def _start_reads(self, delays: tuple[float, ...], newer_than: float | None) -> None:
        self._cancel_reads()
        self._read_task = self.hass.async_create_task(self._read_until_taken(delays, newer_than))

    async def _read_until_taken(self, delays: tuple[float, ...], newer_than: float | None) -> None:
        for delay in delays:
            if delay:
                await asyncio.sleep(delay)
            if await self.async_refresh_from_station(newer_than):
                return

    async def async_refresh_from_station(self, newer_than: float | None = None) -> bool:
        """Take the newest session's figure from the station; True once it has
        answered for good. False, with the shown value kept, when the read
        failed or the newest session ended before ``newer_than`` (not stored
        yet, or none): worth another try. Without ``newer_than`` a station that
        has no session yet is a final answer."""
        session = await self._updater.async_read_last_session()
        if session is None:
            return False
        if session["stop_at"] is None:
            return newer_than is None
        if newer_than is not None and session["stop_at"] < newer_than:
            return False
        value = session[self._key]
        attrs = {"finished_at": datetime.fromtimestamp(session["stop_at"], tz=timezone.utc).isoformat()}
        if value != self._attr_native_value or attrs != self._attr_extra_state_attributes:
            self._attr_native_value = value
            self._attr_extra_state_attributes = attrs
            if self.hass is not None:
                self.async_write_ha_state()
        return True


def create_reve_last_session_sensors(updater, device_number: int = 1) -> list[_ReveLastSessionVoltageSensor]:
    return [
        _ReveLastSessionVoltageSensor(
            updater, "Last Session Min Voltage", "min", "mdi:flash-triangle-outline", device_number
        ),
        _ReveLastSessionVoltageSensor(
            updater, "Last Session Max Voltage", "max", "mdi:flash-triangle", device_number
        ),
    ]


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
