"""Binary sensors for the Eveus integration."""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, FrozenSet

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
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


@dataclass(frozen=True, kw_only=True)
class EveusBinaryDescription:
    """Description for Eveus binary sensor entities."""

    name: str
    device_class: BinarySensorDeviceClass
    icon: str
    is_on_fn: Callable[[dict], bool | None]
    entity_category: EntityCategory | None = None


def _car_connected_is_on(data: dict) -> bool | None:
    """Return whether a car is connected, or None if state is unknown."""
    state = get_safe_value(data, "state", int)
    if state is None or state not in CHARGING_STATES:
        return None
    if state in _PLUG_UNKNOWN_STATES:
        return None
    return state in _CONNECTED_STATES


def _session_active_is_on(data: dict) -> bool | None:
    state = get_safe_value(data, "state", int)
    if state is None or state not in CHARGING_STATES:
        return None
    if state in _PLUG_UNKNOWN_STATES:
        # In the error state the firmware cannot tell whether a session is
        # still active; reporting a definite "off" would falsely trigger
        # session-ended automations. Mirrors Car Connected.
        return None
    return state in SESSION_ACTIVE_STATES


def _ocpp_connected_is_on(data: dict) -> bool | None:
    """Return whether the OCPP backend link is up, or None if unknown."""
    value = get_safe_value(data, "ocppconnected", int)
    if value not in (0, 1):
        return None
    return value == 1


BINARY_SENSORS: Final[tuple[EveusBinaryDescription, ...]] = (
    EveusBinaryDescription(
        name="Car Connected",
        device_class=BinarySensorDeviceClass.PLUG,
        icon="mdi:ev-plug-type2",
        is_on_fn=_car_connected_is_on,
    ),
    EveusBinaryDescription(
        name="Session Active",
        device_class=BinarySensorDeviceClass.RUNNING,
        icon="mdi:ev-station",
        is_on_fn=_session_active_is_on,
    ),
    EveusBinaryDescription(
        name="OCPP Connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        icon="mdi:cloud-check",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=_ocpp_connected_is_on,
    ),
)

_CAR_CONNECTED_DESCRIPTION = BINARY_SENSORS[0]
_SESSION_ACTIVE_DESCRIPTION = BINARY_SENSORS[1]
_OCPP_CONNECTED_DESCRIPTION = BINARY_SENSORS[2]


class EveusBinarySensor(WriteOnChangeMixin, BaseEveusEntity, BinarySensorEntity):
    """Description-driven Eveus binary sensor."""

    def __init__(
        self,
        updater,
        description: EveusBinaryDescription,
        device_number: int = 1,
    ) -> None:
        self._description = description
        self.ENTITY_NAME = description.name
        super().__init__(updater, device_number)
        self._attr_device_class = description.device_class
        self._attr_icon = description.icon
        self._attr_entity_category = description.entity_category
        self._init_write_on_change()

    @property
    def is_on(self) -> bool | None:
        if not self.available:
            return None
        return self._description.is_on_fn(self._updater.data)

    @callback
    def _handle_coordinator_update(self) -> None:
        self._maybe_finalize_device_info()
        self._update_availability_state()
        self._write_if_changed(self.is_on)


class EveusCarConnectedBinarySensor(EveusBinarySensor):
    """Backward-compatible constructor for the car-connected binary sensor."""

    _attr_device_class = BinarySensorDeviceClass.PLUG
    _attr_icon = "mdi:ev-plug-type2"

    def __init__(self, updater, device_number: int = 1) -> None:
        super().__init__(updater, _CAR_CONNECTED_DESCRIPTION, device_number)


class EveusSessionActiveBinarySensor(EveusBinarySensor):
    """Backward-compatible constructor for the session-active binary sensor."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:ev-station"

    def __init__(self, updater, device_number: int = 1) -> None:
        super().__init__(updater, _SESSION_ACTIVE_DESCRIPTION, device_number)


class EveusOcppConnectedBinarySensor(EveusBinarySensor):
    """Backward-compatible constructor for the OCPP-connected binary sensor."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:cloud-check"

    def __init__(self, updater, device_number: int = 1) -> None:
        super().__init__(updater, _OCPP_CONNECTED_DESCRIPTION, device_number)


_BINARY_SENSOR_CLASSES: Final[dict[str, type[EveusBinarySensor]]] = {
    _CAR_CONNECTED_DESCRIPTION.name: EveusCarConnectedBinarySensor,
    _SESSION_ACTIVE_DESCRIPTION.name: EveusSessionActiveBinarySensor,
    _OCPP_CONNECTED_DESCRIPTION.name: EveusOcppConnectedBinarySensor,
}


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EveusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Eveus binary sensors from a config entry."""
    runtime_data = entry.runtime_data
    updater = runtime_data.updater
    device_number = runtime_data.device_number
    async_add_entities(
        [
            _BINARY_SENSOR_CLASSES[description.name](updater, device_number)
            for description in BINARY_SENSORS
        ]
    )
