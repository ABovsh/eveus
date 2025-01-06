"""The Eveus integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.components import input_number

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

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Eveus from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "config": entry.data
    }

    for helper_id, settings in HELPER_SETTINGS.items():
        await input_number.async_setup_helper(
            hass,
            entry,
            helper_id,
            settings
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
