"""Consolidated and optimized utility functions for Eveus integration."""
from __future__ import annotations

import logging
import time
import socket
import re
from functools import lru_cache, wraps
from typing import Any, Callable, TypeVar, Optional, Union, Dict, List
from datetime import datetime, timedelta
from urllib.parse import urlparse
import pytz

from homeassistant.core import State, HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

T = TypeVar('T')

# =============================================================================
# Multi-Device Support Utilities
# =============================================================================

def get_next_device_number(hass: HomeAssistant) -> int:
    """Find the next available device number for multi-device support."""
    existing_numbers = set()
    
    # Check existing config entries for this domain
    for entry in hass.config_entries.async_entries(DOMAIN):
        device_number = entry.data.get("device_number")
        if device_number is not None:
            existing_numbers.add(device_number)
    
    # Find next available number starting from 1
    next_number = 1
    while next_number in existing_numbers:
        next_number += 1
    
    return next_number

def get_device_suffix(device_number: int) -> str:
    """Get device suffix for unique IDs (empty for device 1, number for others)."""
    return "" if device_number == 1 else str(device_number)

def get_device_display_suffix(device_number: int) -> str:
    """Get device suffix for display names (empty for device 1, ' N' for others)."""
    return "" if device_number == 1 else f" {device_number}"

def get_device_identifier(host: str, device_number: int) -> tuple:
    """Get device identifier for device registry (backward compatible)."""
    if device_number == 1:
        # Backward compatibility: first device uses original identifier format
        return (DOMAIN, host)
    else:
        # Additional devices include device number
        return (DOMAIN, f"{host}_{device_number}")

# =============================================================================
# Data Conversion and Validation Utilities
# =============================================================================

def get_safe_value(
    source: Any,
    key: Optional[str] = None,
    converter: Callable[[Any], T] = float,
    default: Optional[T] = None
) -> Optional[T]:
    """Safely extract and convert values with comprehensive error handling.
    
    Optimized version with better performance and error handling.
    """
    try:
        if source is None:
            return default

        # Handle State objects efficiently
        if isinstance(source, State):
            value = source.state
        elif isinstance(source, dict) and key is not None:
            value = source.get(key)
        else:
            value = source

        # Handle unavailable states
        if value in (None, 'unknown', 'unavailable', ''):
            return default

        # Convert value
        return converter(value)
        
    except (TypeError, ValueError, AttributeError) as err:
        _LOGGER.debug("Safe value conversion failed for %s: %s", 
                     f"{source}.{key}" if key else source, err)
        return default

def validate_required_values(*values: Any) -> bool:
    """Validate all required values are present and valid.
    
    Optimized for performance with early exit.
    """
    return not any(v in (None, 'unknown', 'unavailable', '') for v in values)

@lru_cache(maxsize=128)
def safe_float_cached(value: Union[str, int, float], default: float = 0.0) -> float:
    """Cached safe float conversion for frequently used values."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

@lru_cache(maxsize=64)
def safe_int_cached(value: Union[str, int, float], default: int = 0) -> int:
    """Cached safe integer conversion."""
    try:
        return int(float(value))  # Handle string floats like "1.0"
    except (TypeError, ValueError):
        return default

# =============================================================================
# Device Information and Networking
# =============================================================================

def get_device_info(host: str, data: Dict[str, Any], device_number: int = 1) -> Dict[str, Any]:
    """Get standardized device information with optimized data extraction and multi-device support."""
    # Use safe getters with fallbacks
    firmware = (data.get('verFWMain') or data.get('firmware') or 'Unknown').strip()
    hardware = (data.get('verFWWifi') or data.get('hardware') or 'Unknown').strip()
    
    # Ensure minimum viable values
    if len(firmware) < 2:
        firmware = "Unknown"
    if len(hardware) < 2:
        hardware = "Unknown"
    
    # Get device-specific naming
    device_suffix = get_device_display_suffix(device_number)
    device_identifier = get_device_identifier(host, device_number)
        
    return {
        "identifiers": {device_identifier},
        "name": f"Eveus EV Charger{device_suffix}",
        "manufacturer": "Eveus",
        "model": "Eveus EV Charger",
        "sw_version": firmware,
        "hw_version": hardware,
        "configuration_url": f"http://{host}",
    }

@lru_cache(maxsize=32)
def is_valid_ip(ip: str) -> bool:
    """Cached IP address validation."""
    try:
        socket.inet_aton(ip)
        return True
    except socket.error:
        return False

@lru_cache(maxsize=32)
def is_valid_hostname(hostname: str) -> bool:
    """Cached hostname validation."""
    if len(hostname) > 255:
        return False
        
    if hostname[-1] == ".":
        hostname = hostname[:-1]
        
    allowed = re.compile(r"(?!-)[A-Z\d-]{1,63}(?<!-)$", re.IGNORECASE)
    return all(allowed.match(x) for x in hostname.split("."))

def validate_host(host: str) -> str:
    """Validate and clean host input."""
    host = host.strip()
    if not host:
        raise ValueError("Host cannot be empty")
    
    # Remove protocol if present
    if host.startswith(("http://", "https://")):
        parsed = urlparse(host)
        host = parsed.hostname or host

    # Validate IP or hostname
    if not is_valid_ip(host) and not is_valid_hostname(host):
        raise ValueError("Invalid IP address or hostname")
        
    return host

def validate_credentials(username: str, password: str) -> tuple[str, str]:
    """Validate credentials with security checks."""
    username = username.strip()
    password = password.strip()
    
    if not username or not password:
        raise ValueError("Username and password cannot be empty")
    
    if len(username) > 32 or len(password) > 64:
        raise ValueError("Credentials exceed maximum length")
        
    return username, password

# =============================================================================
# Time and Date Utilities
# =============================================================================

@lru_cache(maxsize=64)
def is_dst(timezone_str: str, timestamp: float) -> bool:
    """Cached DST check for performance."""
    try:
        tz = pytz.timezone(timezone_str)
        dt = datetime.fromtimestamp(timestamp, tz=pytz.UTC)
        return bool(dt.astimezone(tz).dst())
    except Exception as err:
        _LOGGER.error("Error checking DST for %s: %s", timezone_str, err)
        return False

def format_duration(seconds: int) -> str:
    """Format duration in seconds to human readable string - original behavior."""
    try:
        if seconds <= 0:
            return "0m"
            
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        minutes = (seconds % 3600) // 60
        
        if days > 0:
            return f"{days}d {hours:02d}h {minutes:02d}m"
        elif hours > 0:
            return f"{hours}h {minutes:02d}m"
        return f"{minutes}m"
    except (TypeError, ValueError):
        return "0m"

def get_system_time_corrected(
    timestamp: int, 
    timezone_str: str, 
    base_offset: int = 7200
) -> Optional[str]:
    """Get system time with timezone correction optimized for EV charger."""
    try:
        if not timestamp or not timezone_str:
            return None

        # Convert to UTC datetime
        dt_utc = datetime.fromtimestamp(timestamp, tz=pytz.UTC)
        
        # Apply DST-aware correction
        offset = base_offset
        if is_dst(timezone_str, timestamp):
            offset += 3600
        
        # Correct timestamp and convert to local time
        corrected_timestamp = timestamp - offset
        dt_corrected = datetime.fromtimestamp(corrected_timestamp, tz=pytz.UTC)
        
        local_tz = pytz.timezone(timezone_str)
        dt_local = dt_corrected.astimezone(local_tz)
        
        return dt_local.strftime("%H:%M")
        
    except Exception as err:
        _LOGGER.error("Error converting system time: %s", err)
        return None

# =============================================================================
# EV Calculation Utilities
# =============================================================================

@lru_cache(maxsize=64)
def calculate_soc_kwh_cached(
    initial_soc: float,
    battery_capacity: float,
    energy_charged: float,
    efficiency_loss: float
) -> float:
    """Cached SOC calculation in kWh for performance."""
    try:
        initial_kwh = (initial_soc / 100) * battery_capacity
        efficiency = (1 - efficiency_loss / 100)
        charged_kwh = energy_charged * efficiency
        total_kwh = initial_kwh + charged_kwh
        return round(max(0, min(total_kwh, battery_capacity)), 2)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0

@lru_cache(maxsize=64)
def calculate_soc_percent_cached(
    initial_soc: float,
    battery_capacity: float,
    energy_charged: float,
    efficiency_loss: float
) -> float:
    """Cached SOC percentage calculation."""
    try:
        if battery_capacity <= 0:
            return initial_soc
            
        soc_kwh = calculate_soc_kwh_cached(
            initial_soc, battery_capacity, energy_charged, efficiency_loss
        )
        percentage = (soc_kwh / battery_capacity) * 100
        return round(max(0, min(percentage, 100)), 0)
    except (TypeError, ValueError, ZeroDivisionError):
        return initial_soc or 0

def calculate_remaining_time(
    current_soc: Union[float, int],
    target_soc: Union[float, int],
    power_meas: Union[float, int],
    battery_capacity: Union[float, int],
    correction: Union[float, int]
) -> str:
    """Calculate remaining time with proper handling of target reached state."""
    try:
        # Double check inputs
        if current_soc is None or target_soc is None or power_meas is None or battery_capacity is None:
            _LOGGER.debug("Missing inputs for remaining time calculation")
            return "unavailable"
            
        # Convert to float to ensure correct math
        current_soc = float(current_soc)
        target_soc = float(target_soc)
        power_meas = float(power_meas)
        battery_capacity = float(battery_capacity)
        correction = float(correction) if correction is not None else 7.5

        # Validate ranges
        if current_soc < 0 or current_soc > 100:
            _LOGGER.debug("Current SOC out of range: %s", current_soc)
            return "unavailable"
            
        if target_soc < 0 or target_soc > 100:
            _LOGGER.debug("Target SOC out of range: %s", target_soc)
            return "unavailable"
            
        if battery_capacity <= 0:
            _LOGGER.debug("Invalid battery capacity: %s", battery_capacity)
            return "unavailable"

        # Calculate energy needed
        remaining_kwh = (target_soc - current_soc) * battery_capacity / 100
        
        # Handle case when target is already reached or exceeded
        if remaining_kwh <= 0:
            return "Target reached"

        # Handle non-charging state
        if power_meas <= 0:
            return "Not charging"

        # Account for efficiency loss
        efficiency = (1 - correction / 100)
        power_kw = power_meas * efficiency / 1000
        
        if power_kw <= 0:
            return "Not charging"

        # Calculate time in minutes then convert to seconds
        total_minutes = round((remaining_kwh / power_kw * 60), 0)
        
        if total_minutes < 1:
            return "< 1m"

        return format_duration(int(total_minutes * 60))

    except Exception as err:
        _LOGGER.error("Error calculating remaining time: %s", err, exc_info=True)
        return "unavailable"

# =============================================================================
# Performance Utilities
# =============================================================================

def performance_monitor(func: Callable) -> Callable:
    """Decorator to monitor function performance.""" 
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            execution_time = time.time() - start_time
            if execution_time > 0.1:  # Log slow operations
                _LOGGER.debug("Slow operation %s: %.3fs", func.__name__, execution_time)
    return wrapper

def batch_process(items: List[Any], batch_size: int = 50) -> List[List[Any]]:
    """Split items into batches for efficient processing."""
    return [items[i:i + batch_size] for i in range(0, len(items), batch_size)]
