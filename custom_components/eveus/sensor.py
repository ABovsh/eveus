"""Support for Eveus sensors."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .basic_sensors import (
    EveusVoltageSensor,
    EveusCurrentSensor,
    EveusPowerSensor,
    EveusCurrentSetSensor,
    EveusSessionTimeSensor,
    EveusSessionEnergySensor,
    EveusTotalEnergySensor,
)
from .ev_sensors import (
    EVSocKwhSensor,
    EVSocPercentSensor,
    TimeToTargetSocSensor,
)
from .diag_sensors import (
    EveusConnectionErrorsSensor,
    EveusStateSensor,
    EveusSubstateSensor,
    EveusGroundSensor,
    EveusBoxTemperatureSensor,
    EveusPlugTemperatureSensor,
    EveusBatteryVoltageSensor,
    EveusSystemTimeSensor,
)
from .counter_sensors import (
    EveusCounterAEnergySensor,
    EveusCounterACostSensor,
    EveusCounterBEnergySensor,
    EveusCounterBCostSensor,
)

_LOGGER = logging.getLogger(__name__)

SENSOR_TYPES = {
    # Basic measurement sensors
    "voltage": EveusVoltageSensor,
    "current": EveusCurrentSensor,
    "power": EveusPowerSensor,
    "current_set": EveusCurrentSetSensor,
    "session_time": EveusSessionTimeSensor,
    "session_energy": EveusSessionEnergySensor,
    "total_energy": EveusTotalEnergySensor,
    
    # Counter sensors
    "counter_a_energy": EveusCounterAEnergySensor,
    "counter_a_cost": EveusCounterACostSensor,
    "counter_b_energy": EveusCounterBEnergySensor,
    "counter_b_cost": EveusCounterBCostSensor,
    
    # Diagnostic sensors
    "connection_errors": EveusConnectionErrorsSensor,
    "state": EveusStateSensor,
    "substate": EveusSubstateSensor,
    "ground": EveusGroundSensor,
    "box_temperature": EveusBoxTemperatureSensor,
    "plug_temperature": EveusPlugTemperatureSensor,
    "battery_voltage": EveusBatteryVoltageSensor,
    "system_time": EveusSystemTimeSensor,
    
    # EV-specific sensors
    "soc_kwh": EVSocKwhSensor,
    "soc_percent": EVSocPercentSensor,
    "time_to_target": TimeToTargetSocSensor,
}

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Eveus sensors."""
    data = hass.data[DOMAIN][entry.entry_id]
    updater = data["updater"]

    sensors = [
        sensor_class(updater) 
        for sensor_class in SENSOR_TYPES.values()
    ]

    async_add_entities(sensors)
