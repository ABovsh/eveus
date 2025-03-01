"""Fallback input entity creator for Eveus integration."""
import logging
import os
import yaml
from typing import Dict, Any, List, Optional
import pathlib

from homeassistant.core import HomeAssistant
from homeassistant.loader import bind_hass
from homeassistant.components.persistent_notification import async_create as async_create_notification
from homeassistant.helpers.storage import Store
from homeassistant.util import slugify
from homeassistant.const import CONF_NAME, CONF_ICON

_LOGGER = logging.getLogger(__name__)

# Define the required input entities
REQUIRED_INPUTS = {
    "ev_battery_capacity": {
        "name": "EV Battery Capacity",
        "min": 10,
        "max": 160,
        "step": 1,
        "initial": 80,
        "unit_of_measurement": "kWh",
        "mode": "slider",
        "icon": "mdi:car-battery",
    },
    "ev_initial_soc": {
        "name": "Initial EV State of Charge",
        "min": 0,
        "max": 100,
        "step": 1,
        "initial": 20,
        "unit_of_measurement": "%",
        "mode": "slider",
        "icon": "mdi:battery-charging-40",
    },
    "ev_soc_correction": {
        "name": "Charging Efficiency Loss",
        "min": 0,
        "max": 15,
        "step": 0.1,
        "initial": 7.5,
        "unit_of_measurement": "%",
        "mode": "slider",
        "icon": "mdi:chart-bell-curve",
    },
    "ev_target_soc": {
        "name": "Target SOC",
        "min": 0,
        "max": 100,
        "step": 10,
        "initial": 80,
        "unit_of_measurement": "%",
        "mode": "slider",
        "icon": "mdi:battery-charging-high",
    },
}

@bind_hass
async def check_and_create_fallback_inputs(hass: HomeAssistant) -> List[str]:
    """Check and create missing input entities using fallback methods."""
    _LOGGER.debug("Checking for required input entities using fallback method")
    
    # Keep track of created entities for logging
    created_entities = []
    missing_entities = []
    
    # Check for existence of each required entity
    for input_id, config in REQUIRED_INPUTS.items():
        entity_id = f"input_number.{input_id}"
        if hass.states.get(entity_id) is None:
            missing_entities.append(input_id)
            _LOGGER.info("Required entity %s does not exist, will create", entity_id)
    
    if not missing_entities:
        _LOGGER.debug("All required input entities exist")
        return []
    
    # Create a notification to inform the user
    notification_message = (
        f"Eveus integration is creating {len(missing_entities)} required input entities: "
        f"{', '.join(['ev_' + entity_id for entity_id in missing_entities])}\n\n"
        "These will be created directly in your configuration."
    )
    await async_create_notification(
        hass,
        notification_message,
        title="Eveus Integration Setup",
        notification_id="eveus_input_fallback_creation",
    )
    
    # Try method 1: Using helpers storage
    created = await create_helpers_via_storage(hass, missing_entities)
    if created:
        created_entities.extend(created)
    else:
        # Try method 2: Updating configuration.yaml directly
        created = await create_helpers_via_yaml(hass, missing_entities)
        if created:
            created_entities.extend(created)
        else:
            # Final fallback: Provide manual instructions
            await provide_manual_instructions(hass, missing_entities)
    
    return created_entities

async def create_helpers_via_storage(hass: HomeAssistant, missing_entities: List[str]) -> List[str]:
    """Create input entities via the helpers storage file."""
    try:
        _LOGGER.debug("Attempting to create entities via helpers storage")
        created = []
        
        # Use Home Assistant's storage system
        store = Store(hass, 1, "core.helper_entries")
        data = await store.async_load() or {"helpers": {}}
        
        # Add missing entities to storage
        for input_id in missing_entities:
            config = REQUIRED_INPUTS[input_id]
            entity_id = f"input_number.{input_id}"
            
            # Create a unique ID
            unique_id = f"eveus_created_{slugify(input_id)}"
            
            # Create the helper entry
            helper_entry = {
                "id": unique_id,
                "type": "input_number",
                "name": config["name"],
                "icon": config.get("icon"),
                "data": {
                    "min": config["min"],
                    "max": config["max"],
                    "step": config["step"],
                    "initial": config["initial"],
                    "unit_of_measurement": config.get("unit_of_measurement"),
                    "mode": config.get("mode", "slider"),
                }
            }
            
            # Add to the helpers dictionary
            data["helpers"][unique_id] = helper_entry
            created.append(entity_id)
        
        # Save the updated storage
        await store.async_save(data)
        _LOGGER.info("Created %d helpers via storage", len(created))
        
        # Force Home Assistant to reload helpers
        await hass.services.async_call(
            "input_number",
            "reload",
            {},
            blocking=True,
        )
        
        return created
    except Exception as err:
        _LOGGER.error("Failed to create entities via storage: %s", err)
        return []

async def create_helpers_via_yaml(hass: HomeAssistant, missing_entities: List[str]) -> List[str]:
    """Create input entities by modifying configuration.yaml."""
    try:
        _LOGGER.debug("Attempting to create entities via configuration.yaml")
        config_path = hass.config.path("configuration.yaml")
        
        if not os.path.exists(config_path):
            _LOGGER.error("Configuration file not found: %s", config_path)
            return []
        
        # Read the current configuration
        with open(config_path, 'r') as file:
            config = yaml.safe_load(file) or {}
        
        # Prepare input_number configuration
        if "input_number" not in config:
            config["input_number"] = {}
        
        # Add missing entities
        created = []
        for input_id in missing_entities:
            entity_config = REQUIRED_INPUTS[input_id]
            config["input_number"][input_id] = entity_config
            created.append(f"input_number.{input_id}")
        
        # Write back the configuration
        with open(config_path, 'w') as file:
            yaml.dump(config, file, default_flow_style=False)
        
        _LOGGER.info("Created %d entities in configuration.yaml", len(created))
        
        # Notify user to restart Home Assistant
        restart_message = (
            f"Eveus integration has added {len(created)} input entities to your configuration.yaml. "
            f"Please restart Home Assistant to apply these changes."
        )
        await async_create_notification(
            hass,
            restart_message,
            title="Home Assistant Restart Required",
            notification_id="eveus_restart_required",
        )
        
        return created
    except Exception as err:
        _LOGGER.error("Failed to create entities via configuration.yaml: %s", err)
        return []

async def provide_manual_instructions(hass: HomeAssistant, missing_entities: List[str]) -> None:
    """Provide manual instructions for creating required entities."""
    _LOGGER.debug("Providing manual instructions for entity creation")
    
    # Build the configuration YAML for the user to manually add
    config_yaml = "input_number:\n"
    for input_id in missing_entities:
        config_yaml += f"  {input_id}:\n"
        for key, value in REQUIRED_INPUTS[input_id].items():
            config_yaml += f"    {key}: {value}\n"
    
    manual_message = (
        "Eveus integration could not automatically create the required input entities. "
        "Please add the following configuration to your configuration.yaml manually:\n\n"
        f"```yaml\n{config_yaml}```\n\n"
        "After adding this configuration, restart Home Assistant."
    )
    
    await async_create_notification(
        hass,
        manual_message,
        title="Manual Configuration Required",
        notification_id="eveus_manual_config",
    )
    
    _LOGGER.warning("Could not automatically create input entities, provided manual instructions")
