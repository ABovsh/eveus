"""Support for Eveus buttons (force refresh)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
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
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        """Refresh is always allowed; the button drives the coordinator itself."""
        return True

    async def async_press(self) -> None:
        """Trigger an immediate (non-debounced) refresh."""
        await self._updater.async_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EveusConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Eveus buttons."""
    runtime_data = entry.runtime_data
    async_add_entities(
        [EveusRefreshButton(runtime_data.updater, runtime_data.device_number)]
    )
