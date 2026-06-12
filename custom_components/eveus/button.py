"""Support for Eveus buttons (force refresh, counter resets, time sync)."""
from __future__ import annotations

import asyncio
import logging
import time

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EveusConfigEntry
from .common_base import BaseEveusEntity

_LOGGER = logging.getLogger(__name__)


class EveusRefreshButton(BaseEveusEntity, ButtonEntity):
    """Button that forces an immediate coordinator refresh.

    Useful for diagnostics and automations like "after my Tesla disconnects,
    force-refresh in 10 s" to get an authoritative snapshot without waiting
    for the next scheduled poll.
    """

    ENTITY_NAME = "Force Refresh"
    _attr_icon = "mdi:refresh"
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def available(self) -> bool:
        """Refresh is always allowed; the button drives the coordinator itself."""
        return True

    async def async_press(self) -> None:
        """Trigger an immediate (non-debounced) refresh."""
        await self._updater.async_force_refresh()
        if not self._updater.last_update_success:
            # Users press this button precisely to verify connectivity; a
            # silent failure would look like a successful check.
            raise HomeAssistantError(
                "Eveus refresh failed: the charger did not respond"
            )


class _EveusResetCounterButton(BaseEveusEntity, ButtonEntity):
    """Momentary action that resets one of the charger's energy counters.

    Modeled as a button (not a switch) because the action is one-shot: there
    is no "on" state to maintain, and HA's switch semantics imply a togglable
    binary state — which a reset is not.
    """

    _attr_icon = "mdi:refresh-circle"
    _attr_entity_category = EntityCategory.CONFIG

    _command: str

    def __init__(self, updater, device_number: int = 1) -> None:
        super().__init__(updater, device_number)
        self._reset_lock = asyncio.Lock()

    async def async_press(self) -> None:
        """Send the reset command. Surfaces a toast in HA on failure."""
        async with self._reset_lock:
            success = await self._updater.send_command(self._command, 0, retry=False)
            if not success:
                raise HomeAssistantError(
                    f"Eveus charger did not accept '{self.name}' reset command"
                )
            _LOGGER.debug("Reset command %s acknowledged", self._command)


class EveusResetCounterAButton(_EveusResetCounterButton):
    """Reset the session-resettable Counter A energy meter."""

    ENTITY_NAME = "Reset Counter A"
    _command = "rstEM1"


class EveusResetCounterBButton(_EveusResetCounterButton):
    """Reset the user-resettable Counter B energy meter."""

    ENTITY_NAME = "Reset Counter B"
    _command = "rstEM2"


class EveusSyncTimeButton(BaseEveusEntity, ButtonEntity):
    """Push the current UTC timestamp to the charger's clock.

    The firmware stores `systemTime` as UTC and renders it as local-as-unix
    (UTC + timeZone*3600) in /main responses. So the correct payload is the
    plain current UTC second count — no timezone arithmetic.
    """

    ENTITY_NAME = "Sync Time"
    _attr_icon = "mdi:clock-check-outline"
    _attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        """Send the current UTC seconds as `systemTime`."""
        utc_now = int(time.time())
        success = await self._updater.send_command("systemTime", utc_now)
        if not success:
            raise HomeAssistantError(
                "Eveus charger did not accept the time-sync command"
            )
        _LOGGER.debug("Time sync sent: systemTime=%d", utc_now)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EveusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Eveus buttons."""
    runtime_data = entry.runtime_data
    updater = runtime_data.updater
    device_number = runtime_data.device_number
    async_add_entities(
        [
            EveusRefreshButton(updater, device_number),
            EveusResetCounterAButton(updater, device_number),
            EveusResetCounterBButton(updater, device_number),
            EveusSyncTimeButton(updater, device_number),
        ]
    )
