"""Entity registration for Eveus integration."""
import logging
from typing import List, Dict, Any, Type

from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def register_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    platform: str,
    entity_list: List[Entity]
) -> None:
    """Register entities for a platform.
    
    Args:
        hass: The Home Assistant instance
        entry: The config entry
        async_add_entities: The entity adder callback
        platform: The platform name (sensor, switch, etc.)
        entity_list: The list of entities to register
    """
    try:
        data = hass.data[DOMAIN][entry.entry_id]
        
        # Initialize entities dict if needed
        if "entities" not in data:
            data["entities"] = {}
        
        # Register entities by platform
        data["entities"][platform] = {
            entity.unique_id: entity for entity in entity_list
        }
        
        # Add entities to Home Assistant
        async_add_entities(entity_list)
        
        _LOGGER.debug(
            "Registered %d entities for %s platform", 
            len(entity_list), 
            platform
        )
        
    except Exception as err:
        _LOGGER.error(
            "Error registering entities for %s platform: %s",
            platform,
            err
        )

def get_updater_from_entry(
    hass: HomeAssistant,
    entry: ConfigEntry
) -> Any:
    """Get updater instance from config entry.
    
    Args:
        hass: The Home Assistant instance
        entry: The config entry
        
    Returns:
        The updater instance or None if not found
    """
    try:
        data = hass.data[DOMAIN][entry.entry_id]
        return data.get("updater")
    except (KeyError, AttributeError) as err:
        _LOGGER.error("Error getting updater: %s", err)
        return None

def get_entity_by_id(
    hass: HomeAssistant,
    entry: ConfigEntry,
    platform: str,
    entity_id: str
) -> Any:
    """Get entity instance by ID.
    
    Args:
        hass: The Home Assistant instance
        entry: The config entry
        platform: The platform name
        entity_id: The entity unique ID
        
    Returns:
        The entity instance or None if not found
    """
    try:
        data = hass.data[DOMAIN][entry.entry_id]
        entities = data.get("entities", {}).get(platform, {})
        return entities.get(entity_id)
    except (KeyError, AttributeError) as err:
        _LOGGER.error("Error getting entity %s: %s", entity_id, err)
        return None
