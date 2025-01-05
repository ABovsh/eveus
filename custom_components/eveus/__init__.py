"""The Eveus integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
import aiohttp

from .const import DOMAIN, LOGGER

PLATFORMS: list[Platform] = [Platform.SENSOR]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Eveus from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Validate connection before setting up platforms
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"http://{entry.data['host']}/main",
                auth=aiohttp.BasicAuth(entry.data["username"], entry.data["password"]),
            ) as response:
                response.raise_for_status()
                await response.json()
    except Exception as err:
        LOGGER.error("Failed to connect to Eveus charger: %s", err)
        raise ConfigEntryNotReady from err

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
