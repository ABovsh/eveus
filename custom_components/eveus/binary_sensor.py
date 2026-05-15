"""Binary sensors for the Eveus integration.

Currently exposes a single `Car Connected` entity derived from the charger's
device-state value. The goal is to remove the most common template-sensor
boilerplate users write on top of `sensor.eveus_state`.
"""
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
from .common_base import BaseEveusEntity
from .utils import get_safe_value

_LOGGER = logging.getLogger(__name__)


# Device-state values that indicate a vehicle is electrically connected.
# Mirrors CHARGING_STATES in const.py: 3=Connected, 4=Charging, 5=Charge
# Complete, 6=Paused. Standby (2), Startup (0), System Test (1), and Error (7)
# explicitly do not imply a plug presence.
_CONNECTED_STATES: Final[FrozenSet[int]] = frozenset({3, 4, 5, 6})


class EveusCarConnectedBinarySensor(BaseEveusEntity, BinarySensorEntity):
    """True whenever the charger reports a vehicle is plugged in.

    The mapping uses canonical device-state values rather than localized state
    strings so it stays stable if the human-readable labels in
    `CHARGING_STATES` ever change.
    """

    ENTITY_NAME = "Car Connected"
    _attr_device_class = BinarySensorDeviceClass.PLUG
    _attr_icon = "mdi:ev-plug-type2"

    @property
    def is_on(self) -> bool | None:
        """Return whether a car is connected, or None if state is unknown."""
        if not self.available:
            return None
        state = get_safe_value(self._updater.data, "state", int)
        if state is None:
            return None
        return state in _CONNECTED_STATES

    @callback
    def _handle_coordinator_update(self) -> None:
        """Write HA state only when availability or plug-state actually changes."""
        previous_state = self.is_on
        self._maybe_finalize_device_info()
        availability_changed = self._update_availability_state()
        if availability_changed or previous_state != self.is_on:
            self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EveusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eveus binary sensors from a config entry."""
    runtime_data = entry.runtime_data
    async_add_entities(
        [
            EveusCarConnectedBinarySensor(
                runtime_data.updater, runtime_data.device_number
            )
        ]
    )
