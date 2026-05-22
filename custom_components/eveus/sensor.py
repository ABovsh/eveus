"""Optimized sensor setup with factory pattern and minimal code."""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EveusConfigEntry
from .sensor_definitions import get_sensor_specifications
from .ev_sensors import (
    ChargingFinishTimeSensor,
    EVSocKwhSensor,
    EVSocPercentSensor,
    TimeToTargetSocSensor,
    InputEntitiesStatusSensor,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EveusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eveus sensors with optimized factory pattern."""
    runtime_data = entry.runtime_data
    updater = runtime_data.updater
    device_number = runtime_data.device_number
    soc_calculator = runtime_data.soc_calculator

    sensor_specs = get_sensor_specifications(phases=runtime_data.phases)
    standard_sensors = [spec.create_sensor(updater, device_number) for spec in sensor_specs]

    ev_sensors = [
        EVSocKwhSensor(updater, device_number, soc_calculator),
        EVSocPercentSensor(updater, device_number, soc_calculator),
        TimeToTargetSocSensor(updater, device_number, soc_calculator),
        ChargingFinishTimeSensor(updater, device_number, soc_calculator),
        InputEntitiesStatusSensor(updater, device_number),
    ]

    sensors = standard_sensors + ev_sensors
    async_add_entities(sensors, update_before_add=False)

    _LOGGER.debug(
        "Created %d sensors (%d standard, %d EV-specific) for %s (device %d)",
        len(sensors),
        len(standard_sensors),
        len(ev_sensors),
        entry.title,
        device_number,
    )
