"""Support for Eveus sensors."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.components.persistent_notification import async_create as async_create_notification

from .const import DOMAIN
from .sensor_registry import get_sensor_definitions
from .ev_sensors import (
    EVSocKwhSensor,
    EVSocPercentSensor,
    TimeToTargetSocSensor,
)
from .common import EveusSensorBase

_LOGGER = logging.getLogger(__name__)

# Define the required input entities with their configurations
REQUIRED_INPUTS = {
    "input_number.ev_battery_capacity": {
        "name": "EV Battery Capacity",
        "min": 10,
        "max": 160,
        "step": 1,
        "initial": 80,
        "unit_of_measurement": "kWh",
        "mode": "slider",
        "icon": "mdi:car-battery",
        "description": "The total capacity of your EV's battery in kWh"
    },
    "input_number.ev_initial_soc": {
        "name": "Initial EV State of Charge",
        "min": 0,
        "max": 100,
        "step": 1,
        "initial": 20,
        "unit_of_measurement": "%",
        "mode": "slider",
        "icon": "mdi:battery-charging-40",
        "description": "The initial state of charge when starting to charge"
    },
    "input_number.ev_soc_correction": {
        "name": "Charging Efficiency Loss",
        "min": 0,
        "max": 15,
        "step": 0.1,
        "initial": 7.5,
        "unit_of_measurement": "%",
        "mode": "slider",
        "icon": "mdi:chart-bell-curve",
        "description": "Efficiency loss during charging (typically 5-10%)"
    },
    "input_number.ev_target_soc": {
        "name": "Target SOC",
        "min": 0,
        "max": 100,
        "step": 5,
        "initial": 80,
        "unit_of_measurement": "%",
        "mode": "slider",
        "icon": "mdi:battery-charging-high",
        "description": "Target state of charge for charging completion"
    },
}

class InputEntitiesStatusSensor(EveusSensorBase):
    """Sensor that monitors the status of required input entities."""

    ENTITY_NAME = "Input Entities Status"
    _attr_icon = "mdi:clipboard-check"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, updater) -> None:
        """Initialize the input status sensor."""
        super().__init__(updater)
        self._state = "Unknown"
        self._missing_entities = []
        self._invalid_entities = []
        self._attr_extra_state_attributes = {}
        self._last_notification_time = 0

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()
        self.async_schedule_update_ha_state(True)

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
            try:
                value = float(state.state)
                if value < 0:  # Simple validation
                    self._invalid_entities.append(entity_id)
            except (ValueError, TypeError):
                self._invalid_entities.append(entity_id)

        # Update the status
        if self._missing_entities:
            self._state = f"Missing {len(self._missing_entities)} Inputs"
            # Generate a notification with instructions for manual creation
            self._notify_missing_inputs()
        elif self._invalid_entities:
            self._state = f"Invalid {len(self._invalid_entities)} Inputs"
        else:
            self._state = "OK"

        # Update attributes
        self._attr_extra_state_attributes = {
            "missing_entities": self._missing_entities,
            "invalid_entities": self._invalid_entities,
            "required_entities": list(REQUIRED_INPUTS.keys()),
            "status_details": self._get_status_details(),
            "configuration_instructions": self._get_configuration_instructions()
        }

    def _notify_missing_inputs(self) -> None:
        """Create a notification with instructions for creating missing input entities."""
        import time
        
        # Only show notification once per hour to avoid spam
        current_time = time.time()
        if current_time - self._last_notification_time < 3600:
            return
            
        self._last_notification_time = current_time
        
        # Generate YAML configuration for missing entities
        config_yaml = "input_number:\n"
        for entity_id in self._missing_entities:
            input_id = entity_id.split(".", 1)[1]  # Remove "input_number." prefix
            config = REQUIRED_INPUTS[entity_id].copy()
            
            # Remove description from YAML
            description = config.pop("description", "")
            
            config_yaml += f"  {input_id}:\n"
            for key, value in config.items():
                if isinstance(value, str):
                    config_yaml += f"    {key}: '{value}'\n"
                else:
                    config_yaml += f"    {key}: {value}\n"
        
        message = (
            f"Eveus integration requires the following {len(self._missing_entities)} input entities "
            f"that are missing from your system:\n\n"
            f"{', '.join(self._missing_entities)}\n\n"
            f"These entities are needed for the SOC calculation features to work correctly.\n\n"
            f"You can create them in one of two ways:\n"
            f"1. Add the following to your configuration.yaml and restart Home Assistant:\n\n"
            f"```yaml\n{config_yaml}```\n\n"
            f"2. Or create them manually through UI:\n"
            f"   Settings > Devices & Services > Helpers > + Create Helper > Number\n\n"
            f"After creating the entities, set appropriate values for your EV."
        )
        
        try:
            self.hass.async_create_task(
                async_create_notification(
                    self.hass,
                    message,
                    title="Eveus Integration - Required Inputs Missing",
                    notification_id="eveus_missing_inputs"
                )
            )
        except Exception as err:
            _LOGGER.error("Failed to create missing inputs notification: %s", err)

    def _get_status_details(self) -> dict[str, Any]:
        """Get detailed status for each required entity."""
        details = {}
        for entity_id, config in REQUIRED_INPUTS.items():
            state = self.hass.states.get(entity_id)
            if state is None:
                details[entity_id] = f"Missing: {config['name']}"
            else:
                try:
                    value = float(state.state)
                    if value < 0:
                        details[entity_id] = f"Invalid: {state.state} ({config['name']})"
                    else:
                        details[entity_id] = f"OK: {state.state} {config.get('unit_of_measurement', '')}"
                except (ValueError, TypeError):
                    details[entity_id] = f"Invalid: {state.state} ({config['name']})"
        return details

    def _get_configuration_instructions(self) -> dict[str, Any]:
        """Get configuration instructions for all required entities."""
        instructions = {}
        for entity_id, config in REQUIRED_INPUTS.items():
            instructions[entity_id] = {
                "name": config["name"],
                "min": config["min"],
                "max": config["max"],
                "step": config["step"],
                "initial": config["initial"],
                "unit": config.get("unit_of_measurement", ""),
                "description": config.get("description", ""),
                "icon": config.get("icon", "")
            }
        return instructions

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        self._check_inputs()  # Check on every state request
        return self._state


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Eveus sensors."""
    data = hass.data[DOMAIN][entry.entry_id]
    updater = data["updater"]

    # Create sensors from definitions
    sensor_definitions = get_sensor_definitions()
    sensors = [definition.create_sensor(updater) for definition in sensor_definitions]
    
    # Add EV-specific calculated sensors
    ev_sensors = [
        EVSocKwhSensor(updater),
        EVSocPercentSensor(updater),
        TimeToTargetSocSensor(updater),
        InputEntitiesStatusSensor(updater),  # Add the input status sensor
    ]
    
    # Add all sensors
    async_add_entities(sensors + ev_sensors)
