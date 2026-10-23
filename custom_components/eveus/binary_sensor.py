"""Binary sensors for the Eveus integration."""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import EveusConfigEntry
from .common_base import BaseEveusEntity, WriteOnChangeMixin
from .reve import FAMILY_CLASSIC, FAMILY_REVE
from .snapshot import EveusSnapshot

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class EveusBinaryDescription:
    """Description for Eveus binary sensor entities."""

    name: str
    device_class: BinarySensorDeviceClass
    icon: str
    is_on_fn: Callable[[EveusSnapshot], bool | None]
    entity_category: EntityCategory | None = None
    # Payload fields the reading comes from; see `BaseEveusEntity._firmware_source_keys`.
    source_keys: tuple[str, ...] = ()


def _car_connected_is_on(snapshot: EveusSnapshot) -> bool | None:
    """Whether a car is connected, or None when the state cannot say.

    Unknown for an unmapped firmware code and for the Error state, where the
    firmware cannot tell whether the plug is still seated. Both rules live on
    the snapshot, so Session Active below cannot answer them differently.
    """
    return snapshot.plugged_in


def _session_active_is_on(snapshot: EveusSnapshot) -> bool | None:
    """Whether a charging session is running, or None when indeterminate.

    In the Error state a definite "off" would falsely trigger session-ended
    automations. Mirrors Car Connected.
    """
    return snapshot.session_active


def _ocpp_connected_is_on(snapshot: EveusSnapshot) -> bool | None:
    """Return whether the OCPP backend link is up, or None if unknown."""
    value = snapshot.get_int("ocppconnected")
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
        source_keys=("ocppconnected",),
    ),
)

CAR_CONNECTED_DESCRIPTION = BINARY_SENSORS[0]
SESSION_ACTIVE_DESCRIPTION = BINARY_SENSORS[1]
OCPP_CONNECTED_DESCRIPTION = BINARY_SENSORS[2]


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
        self._firmware_source_keys = description.source_keys
        # Last reading taken from a SUCCESSFUL poll, republished through the
        # grace window. Kept here rather than derived in the coordinator
        # callback so `is_on` still answers before the first update, the way
        # HA reads it when the entity is added.
        self._last_known_is_on: bool | None = None
        self._init_write_on_change()

    @property
    def is_on(self) -> bool | None:
        if not self.available:
            return None
        if not self._updater.available:
            # Inside the availability grace window the entity is still visible,
            # and the payload behind it is from before the failed poll. Keep
            # publishing that reading rather than a blank: staying available
            # and empty means HA writes `unknown`, which a helper ingests as a
            # real state, where `unavailable` — what this entity becomes when
            # the window closes — is skipped. Same rule as the ordinary
            # sensors; see `BaseEveusEntity._in_availability_grace`.
            return self._last_known_is_on
        self._last_known_is_on = self._description.is_on_fn(self._updater.snapshot)
        return self._last_known_is_on

    @callback
    def _handle_coordinator_update(self) -> None:
        self._maybe_finalize_device_info()
        self._update_availability_state()
        self._write_if_changed(self.is_on)


class EveusReveApiBinarySensor(WriteOnChangeMixin, BaseEveusEntity, BinarySensorEntity):
    """Whether a REVE charger's GraphQL interface answers.

    REVE is read and written through its GraphQL interface, which drops out on
    its own for minutes at a time. While it does, readings come from ``/main``
    and the last good read keeps the schedules and tariffs showing, so this is
    where the outage itself is visible. Created only for REVE.
    """

    ENTITY_NAME = "REVE API"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:api"

    def __init__(self, updater, device_number: int = 1) -> None:
        super().__init__(updater, device_number)
        self._init_write_on_change()

    @property
    def is_on(self) -> bool | None:
        if not self.available:
            return None
        return self._updater.reve_api_answering

    @callback
    def _handle_coordinator_update(self) -> None:
        self._maybe_finalize_device_info()
        self._update_availability_state()
        self._write_if_changed(self.is_on)


class EveusManualModeBinarySensor(WriteOnChangeMixin, BaseEveusEntity, BinarySensorEntity):
    """Whether a REVE station is in manual mode.

    The app's "ignore schedule" Start, One Charge and any Stop put the station
    in manual mode until the car is unplugged: it ignores the schedules and
    every limit. The station reports no flag, so the integration infers it;
    see ``reve.ManualMode``. Created only for REVE.
    """

    ENTITY_NAME = "Manual Mode"
    _attr_icon = "mdi:hand-back-right"

    def __init__(self, updater, device_number: int = 1) -> None:
        super().__init__(updater, device_number)
        self._init_write_on_change()

    @property
    def is_on(self) -> bool | None:
        if not self.available:
            return None
        return self._updater.reve_manual_mode

    @property
    def extra_state_attributes(self) -> dict[str, bool]:
        """``can_turn_off`` where turning Stop or One Charge off ends manual
        mode (REVE_PLUS 1.0.3), so the card can offer Turn off."""
        return {"can_turn_off": True} if self._updater.reve_manual_mode_can_turn_off else {}

    async def _async_restore_state(self, state) -> None:
        """A restart must not end manual mode while the connector stays
        plugged in; the station's state and session age settle the rest."""
        if state.state == "on":
            since = getattr(state, "last_changed", None)
            on_for = None if since is None else (dt_util.utcnow() - since).total_seconds()
            self._updater.reve_restore_manual_mode(on_for)

    @callback
    def _handle_coordinator_update(self) -> None:
        self._maybe_finalize_device_info()
        self._update_availability_state()
        self._write_if_changed((self.is_on, self._updater.reve_manual_mode_can_turn_off))


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
            EveusBinarySensor(updater, description, device_number)
            for description in BINARY_SENSORS
        ]
    )

    # REVE is known only once the charger has answered, which may be long after
    # setup (an entry can be set up with the charger switched off), so the REVE
    # API sensor is added on the first poll that identifies it.
    added = False  # pragma: no mutate - only read for truthiness

    @callback
    def _add_reve_api_sensor() -> None:
        nonlocal added
        if added or getattr(updater, "firmware_family", FAMILY_CLASSIC) != FAMILY_REVE:
            return
        added = True
        async_add_entities(
            [
                EveusReveApiBinarySensor(updater, device_number),
                EveusManualModeBinarySensor(updater, device_number),
            ]
        )

    _add_reve_api_sensor()
    if not added:
        entry.async_on_unload(updater.async_add_listener(_add_reve_api_sensor))
