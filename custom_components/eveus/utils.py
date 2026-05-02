"""Utility functions for Eveus integration."""
from __future__ import annotations

import logging
import math
import time
from collections.abc import Hashable
from typing import Any, Callable, TypeVar, Optional, Union, Dict

from homeassistant.core import State, HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

T = TypeVar('T')


class RateLog:
    """Small helper for rate-limiting repeated log messages."""

    def __init__(self, max_keys: int = 64) -> None:
        """Initialize an optional per-key rate limiter."""
        self._last_log = 0.0
        self._last_logs: dict[Hashable, float] = {}
        self._max_keys = max_keys

    def should_log(self, interval: float, key: Hashable | None = None) -> bool:
        """Return whether a message should be logged for the interval."""
        current_time = time.time()
        if key is None:
            if current_time - self._last_log > interval:
                self._last_log = current_time
                return True
            return False

        last_log = self._last_logs.get(key, 0.0)
        if current_time - last_log <= interval:
            return False

        if key not in self._last_logs and len(self._last_logs) >= self._max_keys:
            oldest_key = min(self._last_logs, key=self._last_logs.get)
            self._last_logs.pop(oldest_key, None)
        self._last_logs[key] = current_time
        return True

# =============================================================================
# Multi-Device Support Utilities
# =============================================================================


def get_next_device_number(hass: HomeAssistant) -> int:
    """Find the next available device number for multi-device support."""
    existing_numbers = set()
    for entry in hass.config_entries.async_entries(DOMAIN):
        device_number = entry.data.get("device_number")
        try:
            if device_number is not None:
                existing_numbers.add(int(device_number))
        except (TypeError, ValueError):
            continue

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

        converted = converter(value)
        if isinstance(converted, float) and not math.isfinite(converted):
            return default
        return converted

    except (TypeError, ValueError, AttributeError):
        return default


# =============================================================================
# Device Information
# =============================================================================


def get_device_info(host: str, data: Dict[str, Any], device_number: int = 1) -> Dict[str, Any]:
    """Get standardized device information with multi-device support."""
    firmware = str(data.get('verFWMain') or data.get('firmware') or 'Unknown').strip()
    hardware = str(data.get('verFWWifi') or data.get('hardware') or 'Unknown').strip()

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


def calculate_soc_kwh_cached(
    initial_soc: float,
    battery_capacity: float,
    energy_charged: float,
    efficiency_loss: float,
) -> float:
    """Backward-compatible wrapper for SOC calculation in kWh."""
    return calculate_soc_kwh(initial_soc, battery_capacity, energy_charged, efficiency_loss)


def calculate_soc_percent_cached(
    initial_soc: float,
    battery_capacity: float,
    energy_charged: float,
    efficiency_loss: float,
) -> float:
    """Backward-compatible wrapper for SOC percentage calculation."""
    return calculate_soc_percent(initial_soc, battery_capacity, energy_charged, efficiency_loss)


def _validate_soc_inputs(
    initial_soc: float,
    battery_capacity: float,
    energy_charged: float,
    efficiency_loss: float,
) -> tuple[float, float, float, float] | None:
    """Validate SOC calculation inputs and return normalized floats."""
    try:
        initial_soc = float(initial_soc)
        battery_capacity = float(battery_capacity)
        energy_charged = float(energy_charged)
        efficiency_loss = float(efficiency_loss)
        if not all(
            math.isfinite(value)
            for value in (initial_soc, battery_capacity, energy_charged, efficiency_loss)
        ):
            return None
        return initial_soc, battery_capacity, energy_charged, efficiency_loss
    except (TypeError, ValueError):
        return None


def calculate_soc_kwh(
    initial_soc: float,
    battery_capacity: float,
    energy_charged: float,
    efficiency_loss: float,
) -> float:
    """Calculate SOC in kWh."""
    inputs = _validate_soc_inputs(
        initial_soc, battery_capacity, energy_charged, efficiency_loss
    )
    if inputs is None:
        return 0.0
    initial_soc, battery_capacity, energy_charged, efficiency_loss = inputs
    if battery_capacity <= 0:
        return 0.0
    initial_kwh = (initial_soc / 100) * battery_capacity
    efficiency = 1 - efficiency_loss / 100
    charged_kwh = energy_charged * efficiency
    total_kwh = initial_kwh + charged_kwh
    return round(max(0, min(total_kwh, battery_capacity)), 2)


def calculate_soc_percent(
    initial_soc: float,
    battery_capacity: float,
    energy_charged: float,
    efficiency_loss: float,
) -> float:
    """Calculate SOC percentage."""
    inputs = _validate_soc_inputs(
        initial_soc, battery_capacity, energy_charged, efficiency_loss
    )
    if inputs is None:
        return initial_soc or 0
    initial_soc, battery_capacity, energy_charged, efficiency_loss = inputs
    if battery_capacity <= 0:
        return initial_soc

    soc_kwh = calculate_soc_kwh(
        initial_soc, battery_capacity, energy_charged, efficiency_loss
    )
    percentage = (soc_kwh / battery_capacity) * 100
    return round(max(0, min(percentage, 100)), 0)


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
