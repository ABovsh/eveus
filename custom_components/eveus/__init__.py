"""The Eveus integration."""
from __future__ import annotations

from dataclasses import dataclass
import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, CONF_HOST, CONF_USERNAME, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, ConfigEntryAuthFailed
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, MODEL_MAX_CURRENT, CONF_MODEL
from .common import EveusUpdater
from .utils import get_next_device_number

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
]

CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Schema({})}, extra=vol.ALLOW_EXTRA)


@dataclass
class EveusRuntimeData:
    """Runtime data for an Eveus config entry."""

    updater: EveusUpdater
    device_number: int
    title: str


EveusConfigEntry = ConfigEntry[EveusRuntimeData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Eveus component."""
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entry data."""
    new_data = dict(entry.data)
    host = new_data.get(CONF_HOST)
    if isinstance(host, str) and host.startswith(("http://", "https://")):
        from .config_flow import validate_host

        try:
            new_data[CONF_HOST] = validate_host(host)
        except vol.Invalid:
            _LOGGER.warning("Could not normalize stored Eveus host %s", host)

    if new_data != entry.data:
        hass.config_entries.async_update_entry(entry, data=new_data)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: EveusConfigEntry) -> bool:
    """Set up Eveus from a config entry."""
    try:
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

        device_number = entry.data.get("device_number")
        if device_number is None:
            device_number = get_next_device_number(hass)
            new_data = dict(entry.data)
            new_data["device_number"] = device_number
            hass.config_entries.async_update_entry(entry, data=new_data)
            _LOGGER.info("Assigned device number %d to %s", device_number, host)

        updater = EveusUpdater(
            host=host,
            username=username,
            password=password,
            hass=hass,
            config_entry=entry,
        )
        entry.runtime_data = EveusRuntimeData(
            updater=updater,
            device_number=device_number,
            title=entry.title,
        )

        await updater.async_config_entry_first_refresh()

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        entry.async_on_unload(entry.add_update_listener(update_listener))

        return True

    except ConfigEntryAuthFailed:
        raise
    except ConfigEntryNotReady:
        raise
    except Exception as ex:
        _LOGGER.error(
            "Unexpected error setting up Eveus integration: %s",
            ex, exc_info=True,
        )
        raise ConfigEntryNotReady(f"Unexpected error: {ex}")


async def update_listener(hass: HomeAssistant, entry: EveusConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: EveusConfigEntry) -> bool:
    """Unload a config entry."""
    try:
        return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    except Exception as ex:
        _LOGGER.error(
            "Error unloading Eveus integration: %s",
            ex, exc_info=True,
        )
        return False
