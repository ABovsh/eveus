"""Optimized sensor setup with factory pattern and minimal code."""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EveusConfigEntry
from .const import get_soc_mode, SOC_MODE_ADVANCED
from .reve import FAMILY_CLASSIC, FAMILY_REVE
from .sensor_definitions import create_sensor, get_sensor_specifications
from .ev_sensors import (
    ChargingFinishTimeSensor,
    CostToTargetSocSensor,
    EnergyToTargetSocSensor,
    EVSocKwhSensor,
    EVSocPercentSensor,
    TimeToTargetSocSensor,
)
from .reve_sensors import create_reve_station_sensors
from .session_history import create_last_session_sensors

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

    # The model maximum travels with the poll (the coordinator was built with
    # this entry's model), so the specs only need the phase count.
    sensor_specs = get_sensor_specifications(phases=runtime_data.phases)
    standard_sensors = [create_sensor(spec, updater, device_number) for spec in sensor_specs]

    ev_sensors: list[object] = []
    if get_soc_mode(entry) == SOC_MODE_ADVANCED:
        ev_sensors = [
            EVSocKwhSensor(updater, device_number, soc_calculator),
            EVSocPercentSensor(updater, device_number, soc_calculator),
            TimeToTargetSocSensor(updater, device_number, soc_calculator),
            ChargingFinishTimeSensor(updater, device_number, soc_calculator),
            EnergyToTargetSocSensor(updater, device_number, soc_calculator),
            CostToTargetSocSensor(
                updater, device_number, soc_calculator, phases=runtime_data.phases
            ),
        ]

    last_session_sensors = create_last_session_sensors(updater, device_number)

    sensors = standard_sensors + ev_sensors + last_session_sensors
    async_add_entities(sensors, update_before_add=False)

    # REVE is known only once the charger has answered, as for the REVE API
    # binary sensor: its station sensors are added on the first poll that
    # identifies it.
    added = False  # pragma: no mutate - only read for truthiness

    @callback
    def _add_reve_station_sensors() -> None:
        nonlocal added
        if added or getattr(updater, "firmware_family", FAMILY_CLASSIC) != FAMILY_REVE:
            return
        added = True
        async_add_entities(create_reve_station_sensors(updater, device_number))

    _add_reve_station_sensors()
    if not added:
        entry.async_on_unload(updater.async_add_listener(_add_reve_station_sensors))

    _LOGGER.debug(
        "Created %d sensors (%d standard, %d EV-specific) for entry %s (device %d)",
        len(sensors),
        len(standard_sensors),
        len(ev_sensors),
        entry.entry_id,
        device_number,
    )
