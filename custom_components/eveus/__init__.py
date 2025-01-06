"""The Eveus integration."""
from __future__ import annotations

import logging
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.components.input_number import (
    DOMAIN as INPUT_NUMBER_DOMAIN,
    InputNumber,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR]

# Input number configurations
INPUT_NUMBERS = {
    "eveus_initial_soc": {
        "name": "Initial EV State of Charge",
        "minimum": 0,
        "maximum": 100,
        "step": 1,
        "mode": "slider",
        "unit_of_measurement": "%",
        "icon": "mdi:battery-charging",
    },
    "eveus_target_soc": {
        "name": "Target SOC",
        "minimum": 80,
        "maximum": 100,
        "step": 10,
        "initial": 80,
        "mode": "slider",
        "unit_of_measurement": "%",
        "icon": "mdi:battery-charging-high",
    },
    "eveus_soc_correction": {
        "name": "SOC Correction Factor",
        "minimum": 0,
        "maximum": 10,
        "step": 0.1,
        "initial": 7.5,
        "mode": "slider",
        "unit_of_measurement": "%",
        "icon": "mdi:tune-variant",
    }
}

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Eveus component."""
    hass.data.setdefault(DOMAIN, {})
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Eveus from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "config": entry.data
    }

    # Get the input_number component
    component = EntityComponent[InputNumber](
        _LOGGER, INPUT_NUMBER_DOMAIN, hass
    )

    # Create input_number entities
    for input_id, config in INPUT_NUMBERS.items():
        try:
            unique_id = f"{entry.entry_id}_{input_id}"
            
            input_entity = InputNumber(
                config["name"],
                config["minimum"],
                config["maximum"],
                config.get("initial"),
                config["step"],
                config.get("mode", "slider"),
                config.get("unit_of_measurement"),
                config.get("icon"),
                unique_id=unique_id
            )
            
            # Add entity to Home Assistant
            await component.async_add_entities([input_entity])
            _LOGGER.debug("Created input_number: %s", input_id)

        except Exception as err:
            _LOGGER.error("Failed to create input_number %s: %s", input_id, err)

    return await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
