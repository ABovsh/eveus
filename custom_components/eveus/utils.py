"""Utility functions for Eveus integration."""
from __future__ import annotations

import logging
from typing import Any, Callable, TypeVar, Optional
from datetime import datetime, timedelta
import pytz

from homeassistant.core import HomeAssistant, State

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

T = TypeVar('T')

def get_safe_value(
    value: Any,
    key: str | None = None,
    converter: Callable[[Any], T] = float,
    default: Optional[T] = None
) -> T | None:
    """Safely get and convert value.
    
    Args:
        value: Value to convert. Can be a State object or direct value
        key: Key to retrieve if value is a dict
        converter: Function to convert the value
        default: Default value if conversion fails
        
    Returns:
        Converted value or default if conversion fails
    """
    try:
        if value is None:
            return default

        # Handle State objects
        if isinstance(value, State):
            value = value.state

        # Handle dictionary values
        if isinstance(value, dict) and key is not None:
            value = value.get(key)

        # Handle unavailable states
        if value in (None, 'unknown', 'unavailable'):
            return default

        return converter(value)
    except (TypeError, ValueError, AttributeError) as err:
        _LOGGER.debug("Error converting value %s: %s", value, err)
        return default

def get_device_info(host: str, data: dict) -> dict[str, Any]:
    """Get standardized device information."""
    return {
        "identifiers": {(DOMAIN, host)},
        "name": "Eveus EV Charger",
        "manufacturer": "Eveus",
        "model": "Eveus EV Charger",
        "sw_version": data.get('verFWMain', 'Unknown').strip(),
        "hw_version": data.get('verFWWifi', 'Unknown').strip(),
        "configuration_url": f"http://{host}",
    }

def validate_required_values(*values: Any) -> bool:
    """Validate all required values are present and not None."""
    return not any(v in (None, 'unknown', 'unavailable') for v in values)

def is_dst(timezone_str: str, dt: datetime) -> bool:
    """Check if the given datetime is in DST for the timezone."""
    try:
        tz = pytz.timezone(timezone_str)
        return dt.astimezone(tz).dst() != timedelta(0)
    except Exception as err:
        _LOGGER.error("Error checking DST: %s", err)
        return False

def format_duration(seconds: int) -> str:
    """Format duration in seconds to human readable string."""
    try:
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        minutes = (seconds % 3600) // 60
        
        if days > 0:
            return f"{days}d {hours:02d}h {minutes:02d}m"
        elif hours > 0:
            return f"{hours}h {minutes:02d}m"
        return f"{minutes}m"
    except (TypeError, ValueError) as err:
        _LOGGER.error("Error formatting duration: %s", err)
        return "0m"

def calculate_remaining_time(
    current_soc: float,
    target_soc: float,
    power_meas: float,
    battery_capacity: float,
    correction: float
) -> str:
    """Calculate remaining time to target SOC."""
    try:
        if not validate_required_values(
            current_soc,
            target_soc,
            power_meas,
            battery_capacity,
            correction
        ):
            return "unavailable"

        if power_meas <= 0:
            return "Not charging"

        remaining_kwh = (target_soc - current_soc) * battery_capacity / 100
        
        if remaining_kwh <= 0:
            return "Target reached"

        efficiency = (1 - correction / 100)
        power_kw = power_meas * efficiency / 1000
        
        if power_kw <= 0:
            return "Not charging"

        total_minutes = round((remaining_kwh / power_kw * 60), 0)
        
        if total_minutes < 1:
            return "< 1m"

        return format_duration(int(total_minutes * 60))

    except Exception as err:
        _LOGGER.error("Error calculating remaining time: %s", err)
        return "unavailable"
