"""Binary sensors for the Eveus integration."""
from __future__ import annotations

import logging
from typing import Final, FrozenSet

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EveusConfigEntry
from .common_base import BaseEveusEntity, WriteOnChangeMixin
from .const import CHARGING_STATES, SESSION_ACTIVE_STATES
from .utils import get_safe_value

_LOGGER = logging.getLogger(__name__)


# Device-state values that indicate a vehicle is electrically connected.
# Mirrors CHARGING_STATES in const.py: 3=Connected, 4=Charging, 5=Charge
# Complete, 6=Paused. Standby (2), Startup (0), System Test (1), and Error (7)
# explicitly do not imply a plug presence.
_CONNECTED_STATES: Final[FrozenSet[int]] = frozenset({3, 4, 5, 6})
_PLUG_UNKNOWN_STATES: Final[FrozenSet[int]] = frozenset({7})


class EveusCarConnectedBinarySensor(WriteOnChangeMixin, BaseEveusEntity, BinarySensorEntity):
    """True whenever the charger reports a vehicle is plugged in."""

    ENTITY_NAME = "Car Connected"
    _attr_device_class = BinarySensorDeviceClass.PLUG
    _attr_icon = "mdi:ev-plug-type2"

    def __init__(self, updater, device_number) -> None:
        super().__init__(updater, device_number)
        self._init_write_on_change()

    @property
    def is_on(self) -> bool | None:
        """Return whether a car is connected, or None if state is unknown."""
        if not self.available:
            return None
        state = get_safe_value(self._updater.data, "state", int)
        if state is None or state not in CHARGING_STATES:
            return None
        if state in _PLUG_UNKNOWN_STATES:
            return None
        return state in _CONNECTED_STATES

    @callback
    def _handle_coordinator_update(self) -> None:
        self._maybe_finalize_device_info()
        self._update_availability_state()
        self._write_if_changed(self.is_on)


class EveusSessionActiveBinarySensor(WriteOnChangeMixin, BaseEveusEntity, BinarySensorEntity):
    """True while a charging session is in progress (Charging or Paused)."""

    ENTITY_NAME = "Session Active"
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:ev-station"

    def __init__(self, updater, device_number) -> None:
        super().__init__(updater, device_number)
        self._init_write_on_change()

    @property
    def is_on(self) -> bool | None:
        if not self.available:
            return None
        state = get_safe_value(self._updater.data, "state", int)
        if state is None or state not in CHARGING_STATES:
            return None
        return state in SESSION_ACTIVE_STATES

    @callback
    def _handle_coordinator_update(self) -> None:
        self._maybe_finalize_device_info()
        self._update_availability_state()
        self._write_if_changed(self.is_on)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EveusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eveus binary sensors from a config entry."""
    runtime_data = entry.runtime_data
    updater = runtime_data.updater
    device_number = runtime_data.device_number
    async_add_entities([
        EveusCarConnectedBinarySensor(updater, device_number),
        EveusSessionActiveBinarySensor(updater, device_number),
    ])
