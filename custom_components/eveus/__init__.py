"""The Eveus integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_platform
from homeassistant.components.input_number import (
    DOMAIN as INPUT_NUMBER_DOMAIN,
    InputNumber,
    CONF_MIN,
    CONF_MAX,
    CONF_STEP,
    CONF_MODE,
    CONF_INITIAL,
    CONF_ICON,
    ATTR_UNIT_OF_MEASUREMENT,
)

from .const import DOMAIN

PLATFORMS: list[Platform] = [Platform.SENSOR]

_LOGGER = logging.getLogger(__name__)

INPUT_NUMBERS = {
    "eveus_initial_soc": {
        "name": "Initial EV State of Charge",
        CONF_MIN: 0,
        CONF_MAX: 100,
        CONF_STEP: 1,
        CONF_MODE: "slider",
        ATTR_UNIT_OF_MEASUREMENT: "%",
        CONF_ICON: "mdi:battery-charging",
    },
    "eveus_target_soc": {
        "name": "Target SOC",
        CONF_MIN: 80,
        CONF_MAX: 100,
        CONF_STEP: 10,
        CONF_INITIAL: 80,
        CONF_MODE: "slider",
        ATTR_UNIT_OF_MEASUREMENT: "%",
        CONF_ICON: "mdi:battery-charging-high",
    },
    "eveus_soc_correction": {
        "name": "SOC Correction Factor",
        CONF_MIN: 0,
        CONF_MAX: 10,
        CONF_STEP: 0.1,
        CONF_INITIAL: 7.5,
        CONF_MODE: "slider",
        ATTR_UNIT_OF_MEASUREMENT: "%",
        CONF_ICON: "mdi:tune-variant",
    },
}

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Eveus from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "config": entry.data
    }

    # Create input_number entities
    for input_id, config in INPUT_NUMBERS.items():
        input_entity_id = f"input_number.{input_id}"
        
        # Check if input_number already exists
        if input_entity_id not in hass.states.async_entity_ids(INPUT_NUMBER_DOMAIN):
            _LOGGER.debug("Creating input_number entity: %s", input_entity_id)
            
            input_config = {
                "platform": INPUT_NUMBER_DOMAIN,
                "name": config["name"],
                "min": config[CONF_MIN],
                "max": config[CONF_MAX],
                "step": config[CONF_STEP],
                "mode": config[CONF_MODE],
                "unit_of_measurement": config.get(ATTR_UNIT_OF_MEASUREMENT),
                "icon": config.get(CONF_ICON),
            }
            
            if CONF_INITIAL in config:
                input_config["initial"] = config[CONF_INITIAL]

            try:
                component = hass.data[INPUT_NUMBER_DOMAIN]
                entity = InputNumber(hass, input_id, input_config)
                if hasattr(component, "async_add_entities"):
                    await component.async_add_entities([entity])
                _LOGGER.debug("Successfully created input_number: %s", input_entity_id)
            except Exception as err:
                _LOGGER.error("Failed to create input_number %s: %s", input_entity_id, err)

    return await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
