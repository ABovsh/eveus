"""The Eveus integration."""
from __future__ import annotations

import logging
import asyncio
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, CONF_HOST, CONF_USERNAME, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady, ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, MODEL_MAX_CURRENT, CONF_MODEL
from .common import EveusUpdater, EveusConnectionError

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
]

CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Schema({})}, extra=vol.ALLOW_EXTRA)

async def async_validate_connection(
    hass: HomeAssistant, 
    host: str, 
    username: str, 
    password: str
) -> None:
    """Validate connection to Eveus device."""
    session = async_get_clientsession(hass)
    
    try:
        async with session.post(
            f"http://{host}/main",
            auth=aiohttp.BasicAuth(username, password),
            timeout=15
        ) as response:
            response.raise_for_status()
            if response.status == 401:
                raise ConfigEntryAuthFailed("Invalid authentication")
            await response.json()  # Validate response format
            
    except aiohttp.ClientResponseError as err:
        if err.status == 401:
            raise ConfigEntryAuthFailed("Invalid authentication")
        raise ConfigEntryNotReady(f"Connection error: {err}")
    except aiohttp.ClientError as err:
        raise ConfigEntryNotReady(f"Connection error: {err}")
    except asyncio.TimeoutError:
        raise ConfigEntryNotReady("Connection timeout")
    except ValueError as err:
        raise ConfigEntryNotReady(f"Invalid response format: {err}")


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Eveus component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Eveus from a config entry."""
    try:
        # Extract and validate configuration
        host = entry.data.get(CONF_HOST)
        username = entry.data.get(CONF_USERNAME)
        password = entry.data.get(CONF_PASSWORD)
        model = entry.data.get(CONF_MODEL)
        
        if not host:
            raise ConfigEntryNotReady("No host specified")
        if not username:
            raise ConfigEntryNotReady("No username specified")
        if not password:
            raise ConfigEntryNotReady("No password specified")
        if model not in MODEL_MAX_CURRENT:
            raise ConfigEntryNotReady("Invalid model specified")

        # Validate connection
        await async_validate_connection(hass, host, username, password)
        
        # Initialize data structure
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][entry.entry_id] = {
            "title": entry.title,
            "host": host,
            "username": username,
            "password": password,
            "entities": {},
        }
        
        # Create updater instance
        try:
            updater = EveusUpdater(
                host=host,
                username=username,
                password=password,
                hass=hass,
            )
            hass.data[DOMAIN][entry.entry_id]["updater"] = updater
        except Exception as err:
            raise ConfigEntryNotReady(f"Failed to initialize updater: {err}")

        # Set up platforms
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        
        # Register update listener
        entry.async_on_unload(entry.add_update_listener(update_listener))
        
        return True

    except ConfigEntryAuthFailed as auth_err:
        _LOGGER.error("Authentication failed: %s", str(auth_err))
        raise
    except ConfigEntryNotReady as ready_err:
        _LOGGER.error("Integration not ready: %s", str(ready_err))
        raise
    except EveusConnectionError as conn_err:
        _LOGGER.error("Connection error: %s", str(conn_err))
        raise ConfigEntryNotReady(f"Connection error: {str(conn_err)}")
    except Exception as ex:
        _LOGGER.error(
            "Unexpected error setting up Eveus integration: %s",
            str(ex),
            exc_info=True
        )
        raise ConfigEntryNotReady(f"Unexpected error: {str(ex)}")


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    try:
        # Get updater instance
        data = hass.data[DOMAIN].get(entry.entry_id, {})
        updater = data.get("updater")
        
        # Unload platforms
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        
        # Shutdown updater if exists
        if updater:
            try:
                await updater.async_shutdown()
            except Exception as err:
                _LOGGER.error("Error shutting down updater: %s", err)
        
        # Clean up data structure
        if unload_ok and entry.entry_id in hass.data[DOMAIN]:
            hass.data[DOMAIN].pop(entry.entry_id)
        
        return unload_ok

    except Exception as ex:
        _LOGGER.error(
            "Error unloading Eveus integration: %s", 
            str(ex),
            exc_info=True
        )
        return False
