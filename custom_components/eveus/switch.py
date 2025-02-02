"""Support for Eveus switches."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.const import (
    CONF_HOST,
    CONF_USERNAME,
    CONF_PASSWORD,
)

from .const import DOMAIN
from .common import BaseEveusEntity, EveusUpdater

_LOGGER = logging.getLogger(__name__)

class BaseSwitchEntity(BaseEveusEntity, SwitchEntity):
    """Base switch entity for Eveus."""

    _attr_entity_category = EntityCategory.CONFIG
    _command: str = None
    _state_key: str = None

    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the switch."""
        super().__init__(updater)
        self._is_on = False

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        try:
            value = self._updater.data.get(self._state_key)
            if value is not None:
                self._is_on = bool(int(value))
            return self._is_on
        except (TypeError, ValueError):
            return self._is_on

    async def _async_restore_state(self, state: State) -> None:
        """Restore previous state."""
        try:
            if state.state == "on":
                self._is_on = True
                await self.async_turn_on()
            elif state.state == "off":
                self._is_on = False
                await self.async_turn_off()
        except Exception as err:
            _LOGGER.error("%s: Error restoring state: %s", self.name, err)

class EveusStopChargingSwitch(BaseSwitchEntity):
    """Representation of Eveus charging control switch."""

    _attr_name = "Stop Charging"
    _attr_icon = "mdi:ev-station"
    _command = "evseEnabled"
    _state_key = "evseEnabled"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on charging."""
        if await self._updater.send_command(self._command, 1):
            self._is_on = True
            self.async_write_ha_state()
        else:
            _LOGGER.error("%s: Failed to set state to 1", self.name)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off charging."""
        if await self._updater.send_command(self._command, 0):
            self._is_on = False
            self.async_write_ha_state()
        else:
            _LOGGER.error("%s: Failed to set state to 0", self.name)

class EveusOneChargeSwitch(BaseSwitchEntity):
    """Representation of Eveus one charge switch."""

    _attr_name = "One Charge"
    _attr_icon = "mdi:lightning-bolt"
    _command = "oneCharge"
    _state_key = "oneCharge"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable one charge mode."""
        if await self._updater.send_command(self._command, 1):
            self._is_on = True
            self.async_write_ha_state()
        else:
            _LOGGER.error("%s: Failed to set state to 1", self.name)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable one charge mode."""
        if await self._updater.send_command(self._command, 0):
            self._is_on = False
            self.async_write_ha_state()
        else:
            _LOGGER.error("%s: Failed to set state to 0", self.name)

class EveusResetCounterASwitch(BaseSwitchEntity):
    """Representation of Eveus reset counter A switch."""

    _attr_name = "Reset Counter A"
    _attr_icon = "mdi:counter"
    _command = "rstEM1"
    _state_key = "IEM1"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Reset counter A."""
        if await self._updater.send_command(self._command, 0):
            self._is_on = False
            self.async_write_ha_state()
        else:
            _LOGGER.error("%s: Failed to reset counter", self.name)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Reset command for off state."""
        if await self._updater.send_command(self._command, 0):
            self._is_on = False
            self.async_write_ha_state()
        else:
            _LOGGER.error("%s: Failed to reset counter", self.name)

    @property
    def is_on(self) -> bool:
        """Return true if counter has value."""
        try:
            value = self._updater.data.get(self._state_key)
            if value is not None:
                return float(value) > 0
            return False
        except (TypeError, ValueError):
            return False

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eveus switches."""
    updater = EveusUpdater(
        host=entry.data[CONF_HOST],
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        hass=hass,
    )

    switches = [
        EveusStopChargingSwitch(updater),
        EveusOneChargeSwitch(updater),
        EveusResetCounterASwitch(updater),
    ]

    # Initialize entities dict if needed
    if "entities" not in hass.data[DOMAIN][entry.entry_id]:
        hass.data[DOMAIN][entry.entry_id]["entities"] = {}

    # Store switch references with unique_id as key
    hass.data[DOMAIN][entry.entry_id]["entities"]["switch"] = {
        switch.unique_id: switch for switch in switches
    }

    async_add_entities(switches)
