"""The Eveus integration."""
from __future__ import annotations

import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN

PLATFORMS = [
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
]

_LOGGER = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Eveus component."""
    hass.data.setdefault(DOMAIN, {})
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Eveus from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    
    try:
        # Create the entry data storage
        hass.data[DOMAIN][entry.entry_id] = {
            "title": entry.title,
            "options": dict(entry.options),
            "platforms": {},
            "unload_listeners": [],
        }
        
        # Set up all platforms
        for platform in PLATFORMS:
            try:
                hass.async_create_task(
                    hass.config_entries.async_forward_entry_setup(entry, platform)
                )
            except Exception as platform_error:
                _LOGGER.error(
                    "Failed to setup platform %s for %s: %s",
                    platform,
                    entry.title,
                    str(platform_error),
                )
                return False

        # Register update listener
        entry.async_on_unload(entry.add_update_listener(async_reload_entry))
                
        return True
        
    except Exception as setup_error:
        _LOGGER.error("Failed to setup Eveus integration: %s", str(setup_error))
        raise ConfigEntryNotReady from setup_error

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    try:
        # Unload platforms
        unload_ok = all(
            await asyncio.gather(
                *[
                    hass.config_entries.async_forward_entry_unload(entry, platform)
                    for platform in PLATFORMS
                ]
            )
        )
        
        # Clean up entry data
        if unload_ok:
            # Execute any registered unload listeners
            for listener in hass.data[DOMAIN][entry.entry_id].get("unload_listeners", []):
                listener()
                
            hass.data[DOMAIN].pop(entry.entry_id)
            
        return unload_ok
        
    except Exception as unload_error:
        _LOGGER.error(
            "Error unloading entry %s: %s",
            entry.title,
            str(unload_error),
        )
        return False

async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
