"""Utility functions for Eveus integration."""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Callable, TypeVar, Optional, Union, Dict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

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
    for entry in hass.config_entries.async_entries(DOMAIN):
        device_number = entry.data.get("device_number")
        if device_number is not None:
            existing_numbers.add(device_number)

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
        return (DOMAIN, host)
    return (DOMAIN, f"{host}_{device_number}")


# =============================================================================
# Data Conversion and Validation Utilities
# =============================================================================


def get_safe_value(
    source: Any,
    key: Optional[str] = None,
    converter: Callable[[Any], T] = float,
    default: Optional[T] = None,
) -> Optional[T]:
    """Safely extract and convert values with comprehensive error handling."""
    try:
        if source is None:
            return default

        if isinstance(source, State):
            value = source.state
        elif isinstance(source, dict) and key is not None:
            value = source.get(key)
        else:
            value = source

        if value in (None, 'unknown', 'unavailable', ''):
            return default

        return converter(value)

    except (TypeError, ValueError, AttributeError):
        return default


# =============================================================================
# Device Information
# =============================================================================


def get_device_info(host: str, data: Dict[str, Any], device_number: int = 1) -> Dict[str, Any]:
    """Get standardized device information with multi-device support."""
    firmware = (data.get('verFWMain') or data.get('firmware') or 'Unknown').strip()
    hardware = (data.get('verFWWifi') or data.get('hardware') or 'Unknown').strip()

    if len(firmware) < 2:
        firmware = "Unknown"
    if len(hardware) < 2:
        hardware = "Unknown"

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


# =============================================================================
# Time and Date Utilities
# =============================================================================


def is_dst(timezone_str: str, timestamp: float) -> bool:
    """Check if DST is active, with hour-level caching."""
    return _is_dst_cached(timezone_str, int(timestamp // 3600))


@lru_cache(maxsize=64)
def _is_dst_cached(timezone_str: str, hour_bucket: int) -> bool:
    """Cached DST check keyed by hour bucket for effective cache reuse."""
    try:
        tz = ZoneInfo(timezone_str)
        dt = datetime.fromtimestamp(hour_bucket * 3600, tz=timezone.utc)
        return bool(dt.astimezone(tz).dst())
    except Exception as err:
        _LOGGER.error("Error checking DST for %s: %s", timezone_str, err)
        return False


def format_duration(seconds: int) -> str:
    """Format duration in seconds to human readable string."""
    try:
        if seconds <= 0:
            return "0m"

        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        minutes = (seconds % 3600) // 60

        if days > 0:
            return f"{days}d {hours:02d}h {minutes:02d}m"
        if hours > 0:
            return f"{hours}h {minutes:02d}m"
        return f"{minutes}m"
    except (TypeError, ValueError):
        return "0m"


# =============================================================================
# EV Calculation Utilities
# =============================================================================


@lru_cache(maxsize=64)
def calculate_soc_kwh_cached(
    initial_soc: float,
    battery_capacity: float,
    energy_charged: float,
    efficiency_loss: float,
) -> float:
    """Cached SOC calculation in kWh."""
    try:
        initial_kwh = (initial_soc / 100) * battery_capacity
        efficiency = 1 - efficiency_loss / 100
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
    efficiency_loss: float,
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
    correction: Union[float, int],
) -> str:
    """Calculate remaining time with proper handling of target reached state."""
    try:
        if None in (current_soc, target_soc, power_meas, battery_capacity):
            return "unavailable"

        current_soc = float(current_soc)
        target_soc = float(target_soc)
        power_meas = float(power_meas)
        battery_capacity = float(battery_capacity)
        correction = float(correction) if correction is not None else 7.5

        if not (0 <= current_soc <= 100) or not (0 <= target_soc <= 100):
            return "unavailable"
        if battery_capacity <= 0:
            return "unavailable"

        remaining_kwh = (target_soc - current_soc) * battery_capacity / 100

        if remaining_kwh <= 0:
            return "Target reached"
        if power_meas <= 0:
            return "Not charging"

        power_kw = power_meas * (1 - correction / 100) / 1000
        if power_kw <= 0:
            return "Not charging"

        total_minutes = round(remaining_kwh / power_kw * 60, 0)
        if total_minutes < 1:
            return "< 1m"

        return format_duration(int(total_minutes * 60))

    except Exception as err:
        _LOGGER.error("Error calculating remaining time: %s", err, exc_info=True)
        return "unavailable"
