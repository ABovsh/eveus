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
    SERVICE_SET_VALUE,
)

from .const import DOMAIN

PLATFORMS: list[Platform] = [Platform.SENSOR]

_LOGGER = logging.getLogger(__name__)

INPUT_NUMBERS = {
    "eveus_initial_soc": {
        "name": "Initial EV State of Charge",
        "min": 0,
        "max": 100,
        "step": 1,
        "mode": "slider",
        "unit_of_measurement": "%",
        "icon": "mdi:battery-charging",
    },
    "eveus_target_soc": {
        "name": "Target SOC",
        "min": 80,
        "max": 100,
        "step": 10,
        "initial": 80,
        "mode": "slider",
        "unit_of_measurement": "%",
        "icon": "mdi:battery-charging-high",
    },
    "eveus_soc_correction": {
        "name": "SOC Correction Factor",
        "min": 0,
        "max": 10,
        "step": 0.1,
        "initial": 7.5,
        "mode": "slider",
        "unit_of_measurement": "%",
        "icon": "mdi:tune-variant",
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
        try:
            await hass.services.async_call(
                "input_number",
                "reload",
                target={
                    "entity_id": f"input_number.{input_id}"
                },
            )
            
            # Create the input_number configuration in configuration.yaml
            input_config = {
                input_id: {
                    "name": config["name"],
                    "min": config["min"],
                    "max": config["max"],
                    "step": config["step"],
                    "mode": config["mode"],
                    "unit_of_measurement": config["unit_of_measurement"],
                    "icon": config["icon"],
                }
            }
            if "initial" in config:
                input_config[input_id]["initial"] = config["initial"]

            await hass.services.async_call(
                "input_number",
                "setup",
                service_data=input_config,
                blocking=True,
            )
            _LOGGER.debug("Created input_number: %s", input_id)
        except Exception as err:
            _LOGGER.error("Error creating input_number %s: %s", input_id, err)

    return await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
