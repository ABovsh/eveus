"""The Eveus integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

# Define the integration domain
DOMAIN = "eveus"

PLATFORMS: list[Platform] = [Platform.SENSOR]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Eveus from a config entry."""
    
    # Check if the config entry has already been set up
    if entry.entry_id in hass.data.get(DOMAIN, {}):
        # If the entry is already set up, return False to prevent further setup
        return False
    
    # Initialize the domain data if it does not exist
    hass.data.setdefault(DOMAIN, {})
    # Store the configuration data associated with the entry
    hass.data[DOMAIN][entry.entry_id] = {
        "config": entry.data
    }
    
    # Forward entry setups to the specified platforms
    result = await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    # Return whether the setup was successful
    return result

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    
    # Unload the platforms associated with the entry
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        # Remove the entry's data from the domain
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
