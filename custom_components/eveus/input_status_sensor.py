"""Diagnostic sensor for monitoring required input entities."""
from __future__ import annotations

import logging
from typing import Any, Dict

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.typing import StateType

from .common import EveusSensorBase
from .utils import get_safe_value

_LOGGER = logging.getLogger(__name__)

REQUIRED_INPUTS = [
    "input_number.ev_initial_soc",
    "input_number.ev_battery_capacity",
    "input_number.ev_soc_correction",
    "input_number.ev_target_soc"
]

class InputStatusSensor(EveusSensorBase):
    """Sensor that monitors the status of required input entities."""

    ENTITY_NAME = "Input Status"
    _attr_icon = "mdi:clipboard-check"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, updater) -> None:
        """Initialize the input status sensor."""
        super().__init__(updater)
        self._state = "Unknown"
        self._missing_entities = []
        self._invalid_entities = []
        self._attr_extra_state_attributes = {}
        self._updater.register_update_callback(self._check_inputs)

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()
        self.async_schedule_update_ha_state(True)
        self._check_inputs()

    @callback
    def _check_inputs(self) -> None:
        """Check all required input entities."""
        self._missing_entities = []
        self._invalid_entities = []

        for entity_id in REQUIRED_INPUTS:
            state = self.hass.states.get(entity_id)
            if state is None:
                self._missing_entities.append(entity_id)
                continue

            # Check if the entity has a valid value
            value = get_safe_value(state, converter=float)
            if value is None:
                self._invalid_entities.append(entity_id)

        # Update the status
        if self._missing_entities:
            self._state = f"Missing {len(self._missing_entities)} Inputs"
        elif self._invalid_entities:
            self._state = f"Invalid {len(self._invalid_entities)} Inputs"
        else:
            self._state = "OK"

        # Update attributes
        self._attr_extra_state_attributes = {
            "missing_entities": self._missing_entities,
            "invalid_entities": self._invalid_entities,
            "required_entities": REQUIRED_INPUTS,
            "status_details": self._get_status_details()
        }

        self.async_write_ha_state()

    def _get_status_details(self) -> Dict[str, Any]:
        """Get detailed status for each required entity."""
        details = {}
        for entity_id in REQUIRED_INPUTS:
            state = self.hass.states.get(entity_id)
            if state is None:
                details[entity_id] = "Missing"
            else:
                value = get_safe_value(state, converter=float)
                if value is None:
                    details[entity_id] = f"Invalid: {state.state}"
                else:
                    details[entity_id] = f"OK: {value}"
        return details

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        return self._state
