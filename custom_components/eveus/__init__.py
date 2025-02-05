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
        # Validate configuration
        if not entry.data.get(CONF_HOST):
            raise ConfigEntryNotReady("No host specified")
        if not entry.data.get(CONF_USERNAME):
            raise ConfigEntryNotReady("No username specified")
        if not entry.data.get(CONF_PASSWORD):
            raise ConfigEntryNotReady("No password specified")
        if not entry.data.get(CONF_MODEL) in MODEL_MAX_CURRENT:
            raise ConfigEntryNotReady("Invalid model specified")

        # Validate connection
        await async_validate_connection(
            hass,
            entry.data[CONF_HOST],
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD]
        )
        
        # Initialize data structure
        hass.data.setdefault(DOMAIN, {})
        
        # Create updater instance with retry mechanism
        try:
            updater = EveusUpdater(
                host=entry.data[CONF_HOST],
                username=entry.data[CONF_USERNAME],
                password=entry.data[CONF_PASSWORD],
                hass=hass,
            )
        except Exception as err:
            raise ConfigEntryNotReady(f"Failed to initialize updater: {err}")

        # Store entry data with validation
        entry_data = {
            "title": entry.title,
            "updater": updater,
            "host": entry.data[CONF_HOST],
            "username": entry.data[CONF_USERNAME],
            "password": entry.data[CONF_PASSWORD],
            "entities": {},
        }
        
        hass.data[DOMAIN][entry.entry_id] = entry_data

        # Set up platforms with error handling
        setup_tasks = []
        for platform in PLATFORMS:
            try:
                setup_tasks.append(
                    hass.config_entries.async_forward_entry_setup(entry, platform)
                )
            except Exception as err:
                _LOGGER.error(
                    "Failed to setup platform %s: %s", 
                    platform, 
                    str(err)
                )
                # Continue with other platforms even if one fails
                continue
        
        if setup_tasks:
            await asyncio.gather(*setup_tasks)
        
        # Register update listener for config changes
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
        # Get updater instance with validation
        data = hass.data[DOMAIN].get(entry.entry_id)
        if not data:
            _LOGGER.warning("No data found for entry %s", entry.entry_id)
            return True
            
        updater = data.get("updater")
        if not updater:
            _LOGGER.warning("No updater found for entry %s", entry.entry_id)
            return True

        # Unload platforms
        unload_ok = await asyncio.gather(
            *(
                hass.config_entries.async_forward_entry_unload(entry, platform)
                for platform in PLATFORMS
            ),
            return_exceptions=True
        )
        
        # Check for any platform unload failures
        if any(isinstance(result, Exception) for result in unload_ok):
            _LOGGER.error("Error unloading platforms: %s", unload_ok)
            return False
        
        if updater:
            try:
                await updater.async_shutdown()
            except Exception as err:
                _LOGGER.error("Error shutting down updater: %s", err)
        
        hass.data[DOMAIN].pop(entry.entry_id)
        
        return True

    except Exception as ex:
        _LOGGER.error(
            "Error unloading Eveus integration: %s", 
            str(ex),
            exc_info=True
        )
        return False
