"""The Eveus integration."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, CONF_HOST, CONF_USERNAME, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, MODEL_MAX_CURRENT, CONF_MODEL
from .common_network import EveusUpdater
from .utils import get_next_device_number

if TYPE_CHECKING:
    from .ev_sensors import CachedSOCCalculator

_LOGGER = logging.getLogger(__name__)

CONFIG_ENTRY_VERSION = 2

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.BUTTON,
]


@dataclass
class EveusRuntimeData:
    """Runtime data for an Eveus config entry."""

    updater: EveusUpdater
    device_number: int
    title: str
    soc_calculator: CachedSOCCalculator


EveusConfigEntry = ConfigEntry[EveusRuntimeData]


def _invalid_config_issue_id(entry: ConfigEntry) -> str:
    """Return the repair issue id for an invalid config entry."""
    return f"invalid_config_{entry.entry_id}"


def _create_invalid_config_issue(
    hass: HomeAssistant,
    entry: ConfigEntry,
    reason: str,
) -> None:
    """Create a repair issue for stored setup data that cannot work."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _invalid_config_issue_id(entry),
        data={"entry_id": entry.entry_id, "reason": reason},
        is_fixable=True,
        is_persistent=True,
        issue_domain=DOMAIN,
        severity=ir.IssueSeverity.ERROR,
        translation_key="invalid_config",
    )


def _delete_invalid_config_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clear the invalid-config repair issue if it exists."""
    ir.async_delete_issue(hass, DOMAIN, _invalid_config_issue_id(entry))


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
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

    update_kwargs: dict[str, Any] = {}
    if new_data != entry.data:
        update_kwargs["data"] = new_data

        if getattr(entry, "unique_id", None) == host:
            update_kwargs["unique_id"] = new_data[CONF_HOST]

        if isinstance(entry.title, str) and host in entry.title:
            update_kwargs["title"] = entry.title.replace(host, new_data[CONF_HOST])

    if getattr(entry, "version", 1) < CONFIG_ENTRY_VERSION:
        update_kwargs["version"] = CONFIG_ENTRY_VERSION

    if update_kwargs:
        hass.config_entries.async_update_entry(entry, **update_kwargs)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: EveusConfigEntry) -> bool:
    """Set up Eveus from a config entry."""
    try:
        host = entry.data.get(CONF_HOST)
        username = entry.data.get(CONF_USERNAME)
        password = entry.data.get(CONF_PASSWORD)
        model = entry.data.get(CONF_MODEL)

        if not host:
            _create_invalid_config_issue(hass, entry, "missing_host")
            raise ConfigEntryError("No host specified")
        if not username:
            _create_invalid_config_issue(hass, entry, "missing_username")
            raise ConfigEntryError("No username specified")
        if not password:
            _create_invalid_config_issue(hass, entry, "missing_password")
            raise ConfigEntryError("No password specified")
        if model not in MODEL_MAX_CURRENT:
            _create_invalid_config_issue(hass, entry, "invalid_model")
            raise ConfigEntryError("Invalid model specified")

        _delete_invalid_config_issue(hass, entry)

        raw_device_number = entry.data.get("device_number")
        try:
            device_number = int(raw_device_number)
        except (TypeError, ValueError):
            device_number = None

        if device_number is None or device_number < 1:
            device_number = get_next_device_number(hass)
            new_data = dict(entry.data)
            new_data["device_number"] = device_number
            hass.config_entries.async_update_entry(entry, data=new_data)
            _LOGGER.debug("Assigned device number %d to %s", device_number, host)
        elif raw_device_number != device_number:
            new_data = dict(entry.data)
            new_data["device_number"] = device_number
            hass.config_entries.async_update_entry(entry, data=new_data)
            _LOGGER.debug("Normalized device number %d for %s", device_number, host)

        updater = EveusUpdater(
            host=host,
            username=username,
            password=password,
            hass=hass,
            config_entry=entry,
        )
        from .ev_sensors import CachedSOCCalculator

        entry.runtime_data = EveusRuntimeData(
            updater=updater,
            device_number=device_number,
            title=entry.title,
            soc_calculator=CachedSOCCalculator(),
        )

        await updater.async_config_entry_first_refresh()

        entry.async_on_unload(updater.async_shutdown)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        entry.async_on_unload(entry.add_update_listener(update_listener))

        return True

    except ConfigEntryAuthFailed:
        raise
    except ConfigEntryError:
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
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
