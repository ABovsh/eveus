# __init__.py
"""The Eveus integration."""
from __future__ import annotations

import logging
import importlib

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Eveus component."""
    hass.data.setdefault(DOMAIN, {})
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Eveus from a config entry."""
    try:
        # Import platform module
        platform_module = importlib.import_module('.sensor', __package__)
        await platform_module.async_setup_entry(hass, entry, lambda *args, **kwargs: None)
        return True
    except Exception as err:
        _LOGGER.exception("Error setting up Eveus integration: %s", err)
        return False

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    try:
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        if unload_ok:
            hass.data[DOMAIN].pop(entry.entry_id, None)
        return unload_ok
    except Exception as err:
        _LOGGER.error("Error unloading Eveus integration: %s", err)
        return False
