"""The Eveus integration."""
from __future__ import annotations

import logging
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.components.input_number import DOMAIN as INPUT_NUMBER_DOMAIN
from homeassistant.helpers.service import async_call_from_config

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR]

# Input number configurations
INPUT_NUMBERS = {
    "eveus_initial_soc": {
        "name": "Initial EV State of Charge",
        "min": 0,
        "max": 100,
        "step": 1,
        "initial": 0,
        "mode": "slider",
        "icon": "mdi:battery-charging",
    },
    "eveus_target_soc": {
        "name": "Target SOC",
        "min": 80,
        "max": 100,
        "step": 10,
        "initial": 80,
        "mode": "slider",
        "icon": "mdi:battery-charging-high",
    },
    "eveus_soc_correction": {
        "name": "SOC Correction Factor",
        "min": 0,
        "max": 10,
        "step": 0.1,
        "initial": 7.5,
        "mode": "slider",
        "icon": "mdi:tune-variant",
    },
}


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Eveus component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Eveus from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {"config": entry.data}

    # Create input_number entities dynamically
    for input_id, config in INPUT_NUMBERS.items():
        try:
            await hass.services.async_call(
                INPUT_NUMBER_DOMAIN,
                "set",
                {
                    "entity_id": f"input_number.{input_id}",
                    "name": config["name"],
                    "min": config["min"],
                    "max": config["max"],
                    "step": config["step"],
                    "initial": config["initial"],
                    "icon": config["icon"],
                },
                blocking=True,
            )
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
