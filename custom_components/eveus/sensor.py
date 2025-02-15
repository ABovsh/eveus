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
    EveusConnectionQualitySensor,
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

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Eveus sensors."""
    data = hass.data[DOMAIN][entry.entry_id]
    updater = data["updater"]

    sensors = [
        # Basic measurement sensors
        EveusVoltageSensor(updater),
        EveusCurrentSensor(updater),
        EveusPowerSensor(updater),
        EveusCurrentSetSensor(updater),
        EveusSessionTimeSensor(updater),
        EveusSessionEnergySensor(updater),
        EveusTotalEnergySensor(updater),
              
        # Counter sensors
        EveusCounterAEnergySensor(updater),
        EveusCounterACostSensor(updater),
        EveusCounterBEnergySensor(updater),
        EveusCounterBCostSensor(updater),
        
        # Diagnostic sensors
        EveusConnectionQualitySensor(updater),
        EveusStateSensor(updater),
        EveusSubstateSensor(updater),
        EveusGroundSensor(updater),
        EveusBoxTemperatureSensor(updater),
        EveusPlugTemperatureSensor(updater),
        EveusBatteryVoltageSensor(updater),
        EveusSystemTimeSensor(updater),
        
        # EV-specific sensors
        EVSocKwhSensor(updater),
        EVSocPercentSensor(updater),
        TimeToTargetSocSensor(updater),
    ]

    async_add_entities(sensors)
