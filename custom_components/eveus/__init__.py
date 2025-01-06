"""The Eveus integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR]

HELPER_SETTINGS = {
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
    }
}

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Eveus component."""
    hass.data.setdefault(DOMAIN, {})
    return True

async def create_input_number(hass: HomeAssistant, input_id: str, settings: dict) -> None:
    """Create an input_number entity."""
    service_data = {
        "input_number": {
            input_id: {
                "min": settings["min"],
                "max": settings["max"],
                "name": settings["name"],
                "step": settings["step"],
                "mode": settings["mode"],
                "unit_of_measurement": settings["unit_of_measurement"],
                "icon": settings["icon"],
                "initial": settings.get("initial", (settings["max"] + settings["min"]) / 2),
            }
        }
    }

    try:
        await hass.services.async_call("input_number", "setup", service_data, blocking=True)
        _LOGGER.debug("Successfully created input_number: %s", input_id)
    except Exception as err:
        _LOGGER.error("Failed to create input_number %s: %s", input_id, err)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Eveus from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "config": entry.data
    }

    # Create input_number entities
    for input_id, settings in HELPER_SETTINGS.items():
        await create_input_number(hass, input_id, settings)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
