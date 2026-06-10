"""Utility functions for Eveus integration."""
from __future__ import annotations

import logging
import math
import time
from collections.abc import Hashable
from typing import Any, Callable, TypeVar, Optional, Dict

from homeassistant.core import State, HomeAssistant

from .const import DEFAULT_SOC_CORRECTION, DOMAIN, SOC_INPUT_LIMITS

_LOGGER = logging.getLogger(__name__)

T = TypeVar('T')


def normalize_soc_input(key: str, value, default: float) -> float:
    """Coerce a SOC input to a finite, in-range float.

    Non-numeric / non-finite -> default. Finite but out-of-range -> clamped to
    the SOC_INPUT_LIMITS[key] bounds. Used by migration seeding, config-flow
    prefill consumption, and number construction so a bad value can never reach
    the SOC calculator.
    """
    lo, hi = SOC_INPUT_LIMITS[key]
    # A bool is an int subclass; float(True) is 1.0, which would masquerade as a
    # real 1%/1 kWh seed. Reject it so a stray bool falls back to the default.
    if isinstance(value, bool):
        return float(default)
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        # OverflowError: float() of a multi-thousand-digit JSON/stored integer.
        return float(default)
    if not math.isfinite(v):
        return float(default)
    return float(min(hi, max(lo, v)))


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


def _used_device_numbers(hass: HomeAssistant, exclude_entry_id: str | None = None) -> set:
    """Return device numbers already assigned to other Eveus entries."""
    existing_numbers = set()
    for entry in hass.config_entries.async_entries(DOMAIN):
        if exclude_entry_id is not None and entry.entry_id == exclude_entry_id:
            continue
        device_number = entry.data.get("device_number")
        try:
            if device_number is not None:
                existing_numbers.add(int(device_number))
        except (TypeError, ValueError, OverflowError):
            continue
    return existing_numbers


def get_next_device_number(hass: HomeAssistant, exclude_entry_id: str | None = None) -> int:
    """Find the next available device number for multi-device support."""
    existing_numbers = _used_device_numbers(hass, exclude_entry_id)
    next_number = 1
    while next_number in existing_numbers:
        next_number += 1
    return next_number


def is_device_number_taken(
    hass: HomeAssistant, device_number: int, exclude_entry_id: str | None = None
) -> bool:
    """Return True when another Eveus entry already uses this device number."""
    return device_number in _used_device_numbers(hass, exclude_entry_id)


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

        if isinstance(value, bool) and converter in (float, int):
            return default

        if isinstance(value, float) and not math.isfinite(value):
            return default

        # Integer fields (state, switch flags, schedule minutes, tariff index,
        # timezone offset, ...) must be exact. A fractional float like 4.9 would
        # otherwise truncate to 4 and masquerade as a valid enum value, so reject
        # non-integral floats instead of silently truncating them.
        if converter is int and isinstance(value, float) and not value.is_integer():
            return default

        converted = converter(value)
        if isinstance(converted, float) and not math.isfinite(converted):
            return default
        return converted

    except (TypeError, ValueError, OverflowError, AttributeError):
        return default


# =============================================================================
# Device Information
# =============================================================================


def _safe_str(value: Any, fallback: str = "Unknown", min_len: int = 2) -> str:
    """Coerce a /main field to a trimmed string or a fallback."""
    # bool is an int subclass; reject it and other non-scalar containers so a
    # malformed firmware field (e.g. verFWMain=true) can't render as "True" and
    # get permanently finalized into device_info.
    if value is None or isinstance(value, (bool, dict, list, tuple, set)):
        return fallback
    text = str(value).strip()
    return text if len(text) >= min_len else fallback


def get_device_info(host: str, data: Dict[str, Any], device_number: int = 1, scheme: str = "http") -> Dict[str, Any]:
    """Get standardized device information with multi-device support.

    The charger exposes its own model, manufacturer, and serial in /main. When
    those fields are present we surface them in device_info so the Devices
    page shows real device metadata instead of generic strings.
    """
    firmware = _safe_str(data.get("verFWMain") or data.get("firmware"))
    manufacturer = _safe_str(data.get("manufacturer"), fallback="Eveus")
    model = _safe_str(data.get("model"), fallback="Eveus EV Charger")
    serial = data.get("serialNum") or data.get("stationId")
    # _safe_str rejects bools/containers so a malformed firmware field can't
    # become the literal device serial in the registry.
    serial_str = _safe_str(serial, fallback="") or ""

    device_suffix = get_device_display_suffix(device_number)
    device_identifier = get_device_identifier(host, device_number)

    info: Dict[str, Any] = {
        "identifiers": {device_identifier},
        "name": f"Eveus EV Charger{device_suffix}",
        "manufacturer": manufacturer,
        "model": model,
        "sw_version": firmware,
        # No hw_version: the firmware exposes no hardware-revision field.
        # verFWWifi is Wi-Fi module firmware and is surfaced in diagnostics only.
        "configuration_url": f"{scheme}://{host}",
    }
    if serial_str:
        info["serial_number"] = serial_str
    return info


def format_duration(seconds: int) -> str:
    """Format duration in seconds to human readable string."""
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "0m"
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


# =============================================================================
# EV Calculation Utilities
# =============================================================================


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
        if not 0 <= initial_soc <= 100:
            return None
        if battery_capacity <= 0:
            return None
        if energy_charged < 0:
            return None
        if not 0 <= efficiency_loss < 100:
            return None
        return initial_soc, battery_capacity, energy_charged, efficiency_loss
    except (TypeError, ValueError):
        return None


def _soc_kwh_from_inputs(
    initial_soc: float,
    battery_capacity: float,
    energy_charged: float,
    efficiency_loss: float,
) -> float:
    """Compute clamped SOC kWh from already-validated inputs (no revalidation)."""
    initial_kwh = (initial_soc / 100) * battery_capacity
    charged_kwh = energy_charged * (1 - efficiency_loss / 100)
    total_kwh = initial_kwh + charged_kwh
    return round(max(0, min(total_kwh, battery_capacity)), 2)


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
    return _soc_kwh_from_inputs(*inputs)


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
        return 0.0
    # _validate_soc_inputs guarantees battery_capacity > 0, so a single
    # validation feeds both the kWh figure and the percentage.
    _initial_soc, battery_capacity, _energy, _loss = inputs
    soc_kwh = _soc_kwh_from_inputs(*inputs)
    percentage = (soc_kwh / battery_capacity) * 100
    return round(max(0, min(percentage, 100)), 0)


_REMAINING_TARGET_REACHED = "target_reached"
_REMAINING_NOT_CHARGING = "not_charging"
_REMAINING_UNAVAILABLE = "unavailable"


def _remaining_seconds_or_state(
    current_soc, target_soc, power_meas, battery_capacity, correction,
):
    """Return remaining charging seconds or a sentinel string for special states.

    Returns:
      float seconds (>0)   — still charging, ETA known
      "target_reached"     — current_soc already meets/exceeds target
      "not_charging"       — power is zero/negative
      "unavailable"        — inputs missing or invalid
    """
    try:
        if None in (current_soc, target_soc, power_meas, battery_capacity):
            return _REMAINING_UNAVAILABLE

        current_soc = float(current_soc)
        target_soc = float(target_soc)
        power_meas = float(power_meas)
        battery_capacity = float(battery_capacity)
        correction = float(correction) if correction is not None else DEFAULT_SOC_CORRECTION

        if not (0 <= current_soc <= 100) or not (0 <= target_soc <= 100):
            return _REMAINING_UNAVAILABLE
        if battery_capacity <= 0:
            return _REMAINING_UNAVAILABLE

        remaining_kwh = (target_soc - current_soc) * battery_capacity / 100
        if remaining_kwh <= 0:
            return _REMAINING_TARGET_REACHED
        if power_meas <= 0:
            return _REMAINING_NOT_CHARGING

        power_kw = power_meas * (1 - correction / 100) / 1000
        if power_kw <= 0:
            return _REMAINING_NOT_CHARGING

        # A finite-but-tiny power_kw can overflow the division to +inf; surface
        # that as "unavailable" rather than returning inf (which int() would
        # later reject) or freezing a stale ETA.
        seconds = remaining_kwh / power_kw * 3600
        if not math.isfinite(seconds):
            return _REMAINING_UNAVAILABLE
        return seconds

    except Exception as err:
        _LOGGER.debug("Error computing remaining seconds: %s", err, exc_info=True)
        return _REMAINING_UNAVAILABLE


def calculate_remaining_seconds(
    current_soc, target_soc, power_meas, battery_capacity, correction,
) -> Optional[float]:
    """Return charging seconds until target SOC, or None for special/invalid states.

    Returns:
      seconds > 0  — actively charging, ETA known
      0.0          — target already reached
      None         — not charging or inputs invalid (no meaningful ETA)
    """
    result = _remaining_seconds_or_state(
        current_soc, target_soc, power_meas, battery_capacity, correction,
    )
    if isinstance(result, (int, float)):
        return float(result)
    if result == _REMAINING_TARGET_REACHED:
        return 0.0
    return None


def calculate_remaining_time(
    current_soc: float | int,
    target_soc: float | int,
    power_meas: float | int,
    battery_capacity: float | int,
    correction: float | int,
) -> str:
    """Calculate remaining time as a human-readable string."""
    result = _remaining_seconds_or_state(
        current_soc, target_soc, power_meas, battery_capacity, correction,
    )
    if result == _REMAINING_TARGET_REACHED:
        return "Target reached"
    if result == _REMAINING_NOT_CHARGING:
        return "Not charging"
    if result == _REMAINING_UNAVAILABLE:
        return "unavailable"
    total_minutes = round(result / 60, 0)
    if total_minutes < 1:
        return "< 1m"
    return format_duration(int(total_minutes * 60))
