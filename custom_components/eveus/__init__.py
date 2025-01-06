"""The Eveus integration."""
from __future__ import annotations

import logging
import asyncio

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .sensor import async_setup_entry as async_setup_sensor_entry

PLATFORMS: list[Platform] = [Platform.SENSOR]

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Eveus from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    
    try:
        # Direct async setup instead of using forward_entry_setups
        await async_setup_sensor_entry(hass, entry, lambda x: None)
        return True
    except Exception as err:
        _LOGGER.error("Error setting up Eveus integration: %s", err)
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
