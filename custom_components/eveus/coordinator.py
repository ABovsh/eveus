"""Support for Eveus coordinator."""
from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp
import async_timeout

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.const import CONF_HOST, CONF_USERNAME, CONF_PASSWORD

from .const import DOMAIN, LOGGER, SCAN_INTERVAL

class EveusDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Eveus data."""

    def __init__(self, hass: HomeAssistant, config_entry) -> None:
        """Initialize."""
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.host = config_entry.data[CONF_HOST]
        self.username = config_entry.data[CONF_USERNAME]
        self.password = config_entry.data[CONF_PASSWORD]
        LOGGER.debug(
            "Initialized coordinator for %s with update interval %s",
            self.host,
            SCAN_INTERVAL,
        )

    async def _async_update_data(self):
        """Update data via library."""
        LOGGER.debug("Starting data update for %s", self.host)
        try:
            async with async_timeout.timeout(10):
                async with aiohttp.ClientSession() as session:
                    LOGGER.debug("Making request to %s", self.host)
                    async with session.post(
                        f"http://{self.host}/main",
                        auth=aiohttp.BasicAuth(self.username, self.password),
                        timeout=10,
                    ) as response:
                        if response.status == 401:
                            raise ConfigEntryAuthFailed(
                                "Authentication failed, please check credentials"
                            )
                        response.raise_for_status()
                        data = await response.json()
                        LOGGER.debug(
                            "Successfully fetched data from %s: %s",
                            self.host,
                            str(data)[:100] + "...",  # Log first 100 chars only
                        )
                        return data

        except asyncio.TimeoutError as error:
            LOGGER.error("Timeout error fetching data from %s: %s", self.host, error)
            raise UpdateFailed(f"Timeout error fetching data: {error}") from error
        except aiohttp.ClientResponseError as error:
            LOGGER.error("Response error from %s: %s", self.host, error)
            raise UpdateFailed(f"Error fetching data: {error}") from error
        except Exception as error:
            LOGGER.exception("Unexpected error updating data from %s: %s", self.host, error)
            raise UpdateFailed(f"Error updating data: {error}") from error
