"""Constants for the Eveus integration."""
from typing import Final, Dict, List, Literal

DOMAIN: Final[str] = "eveus"

# Update intervals
CHARGING_UPDATE_INTERVAL: Final[int] = 30
IDLE_UPDATE_INTERVAL: Final[int] = 60
OFFLINE_UPDATE_INTERVAL: Final[int] = 120
RETRY_DELAY: Final[int] = 15
UPDATE_TIMEOUT: Final[int] = 20
COMMAND_TIMEOUT: Final[int] = 25

# Charger device-state value that means "actively charging" (CHARGING_STATES[4]).
DEVICE_STATE_CHARGING: Final[int] = 4
# Charger device-state value that means "error" (CHARGING_STATES[7]).
DEVICE_STATE_ERROR: Final[int] = 7

# Device states that count as "a charging session is in progress": actively
# Charging (4) or briefly Paused (6) mid-session. Excludes Connected (3) where
# the car is plugged in but no session is running, and Charge Complete (5).
SESSION_ACTIVE_STATES: Final[frozenset[int]] = frozenset({4, 6})

# Default SOC efficiency correction (%) when the user has not provided the helper.
DEFAULT_SOC_CORRECTION: Final[float] = 7.5

# Availability and resilience - optimized for WiFi connections
AVAILABILITY_GRACE_PERIOD: Final[int] = 60
CONTROL_GRACE_PERIOD: Final[int] = 30
ERROR_LOG_RATE_LIMIT: Final[int] = 300
STATE_CACHE_TTL: Final[int] = 60
OPTIMISTIC_CONTROL_TTL: Final[int] = 120

# CR2032 coin cell inside the charger (reported as `vBat`). A low reading is
# surfaced as an informational "replace soon" notice (we don't model exactly
# which charger functions depend on it, only that some may be limited). The
# CR2032 discharge curve
# is flat until end of life, then drops off a cliff. The charger
# is observed to run fine down to ~2.1 V, so the warning holds off until below
# 2.0 V — deep on the discharge tail but still at the edge of the RTC's retention
# floor, replace-now territory. The clear threshold sits above the fire threshold
# so a reading hovering at the edge can't flap, and the warning only fires after
# several consecutive low polls so a single glitchy ADC read can't raise a scary
# "replace your battery" notice.
BATTERY_LOW_THRESHOLD_VOLTS: Final[float] = 2.0
BATTERY_OK_THRESHOLD_VOLTS: Final[float] = 2.3
BATTERY_LOW_DEBOUNCE_POLLS: Final[int] = 3

# Safety repair thresholds. Firmware faults trigger immediately; raw telemetry
# requires consecutive valid polls and recovery hysteresis to prevent flapping.
GROUND_TRIGGER_POLLS: Final[int] = 3
GROUND_CLEAR_POLLS: Final[int] = 2
GROUND_CONTROL_TRIGGER_POLLS: Final[int] = 3
GROUND_CONTROL_CLEAR_POLLS: Final[int] = 2

TEMPERATURE_HIGH_C: Final[float] = 85.0
TEMPERATURE_RECOVERED_C: Final[float] = 75.0
TEMPERATURE_TRIGGER_POLLS: Final[int] = 2
TEMPERATURE_RECOVERY_POLLS: Final[int] = 3
MIN_VALID_TEMPERATURE_C: Final[float] = -40.0
MAX_VALID_TEMPERATURE_C: Final[float] = 150.0

LEAKAGE_HIGH_MA: Final[float] = 30.0
LEAKAGE_RECOVERED_MA: Final[float] = 15.0
LEAKAGE_TRIGGER_POLLS: Final[int] = 2
LEAKAGE_RECOVERY_POLLS: Final[int] = 3
MAX_VALID_LEAKAGE_CURRENT_MA: Final[float] = 100_000.0

FAULT_RECOVERY_POLLS: Final[int] = 2

# Current limits
MIN_CURRENT: Final[int] = 7
MODEL_16A: Final[str] = "16A"
MODEL_32A: Final[str] = "32A"
MODEL_48A: Final[str] = "48A"
MODELS: Final[List[str]] = [MODEL_16A, MODEL_32A, MODEL_48A]

MODEL_MAX_CURRENT: Final[Dict[str, int]] = {
    MODEL_16A: 16,
    MODEL_32A: 32,
    MODEL_48A: 48,
}

# Upper sanity ceilings for live telemetry, shared by the display sensors and
# the SOC/ETA calculations so both reject the same corrupt finite outliers
# (a finite but impossible value like 1e100) instead of only the display side.
MAX_POWER_W: Final[int] = 100_000
MAX_ENERGY_KWH: Final[int] = 1_000_000
# Upper sanity cap for session duration (seconds). A charging session never runs
# anywhere near a year; the bound only rejects corrupt outliers that would
# otherwise render an overlong HA state string.
MAX_SESSION_TIME_SECONDS: Final[int] = 366 * 24 * 3600

# Configuration
CONF_MODEL: Final[str] = "model"
CONF_SCHEME: Final[str] = "scheme"
DEFAULT_SCHEME: Final[str] = "http"
CONF_PHASES: Final[str] = "phases"
PHASE_OPTIONS: Final[List[int]] = [1, 3]
DEFAULT_PHASES: Final[int] = 1

# Integration mode (Basic / Advanced); key kept as soc_mode for entry compatibility
CONF_SOC_MODE: Final[str] = "soc_mode"
SOC_MODE_BASIC: Final[str] = "basic"
SOC_MODE_ADVANCED: Final[str] = "advanced"
SOC_MODE_OPTIONS: Final[List[str]] = [SOC_MODE_BASIC, SOC_MODE_ADVANCED]

# SOC input seed keys (stored in entry.data) + defaults
CONF_INITIAL_SOC: Final[str] = "initial_soc"
CONF_TARGET_SOC: Final[str] = "target_soc"
CONF_BATTERY_CAPACITY: Final[str] = "battery_capacity"
CONF_SOC_CORRECTION: Final[str] = "soc_correction"

DEFAULT_INITIAL_SOC: Final[float] = 20
DEFAULT_TARGET_SOC: Final[float] = 80
DEFAULT_BATTERY_CAPACITY: Final[float] = 50
SOC_CORRECTION_MAX: Final[float] = 20

# (min, max) guardrails shared by number ranges and config-flow validation.
SOC_INPUT_LIMITS: Final[Dict[str, tuple]] = {
    "initial_soc": (0, 100),
    "target_soc": (0, 100),
    "battery_capacity": (10, 160),
    "soc_correction": (0, SOC_CORRECTION_MAX),
}


def soc_update_signal(entry_id: str) -> str:
    """Per-entry dispatcher signal fired when a SOC input value changes."""
    return f"eveus_soc_update_{entry_id}"


def get_soc_mode(entry) -> str:
    """Return the SOC mode for a config entry.

    An absent OR invalid stored value resolves to advanced, so a corrupt
    ``soc_mode`` can never silently strip the SOC sensors and number entities.
    """
    mode = entry.data.get(CONF_SOC_MODE, SOC_MODE_ADVANCED)
    return mode if mode in SOC_MODE_OPTIONS else SOC_MODE_ADVANCED


# Rate States
RATE_STATES: Final[Dict[int, str]] = {
    0: "Primary Rate",
    1: "Rate 2",
    2: "Rate 3",
}

# State Mappings
DeviceState = Literal[0, 1, 2, 3, 4, 5, 6, 7]
ErrorState = Literal[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
SubState = Literal[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

CHARGING_STATES: Final[Dict[DeviceState, str]] = {
    0: "Startup",
    1: "System Test",
    2: "Standby",
    3: "Connected",
    4: "Charging",
    5: "Charge Complete",
    6: "Paused",
    7: "Error",
}

ERROR_STATES: Final[Dict[ErrorState, str]] = {
    0: "No Error",
    1: "Grounding Error",
    2: "Current Leak High",
    3: "Relay Error",
    4: "Current Leak Low",
    5: "Box Overheat",
    6: "Plug Overheat",
    7: "Pilot Error",
    8: "Low Voltage",
    9: "Diode Error",
    10: "Overcurrent",
    11: "Interface Timeout",
    12: "Software Failure",
    13: "GFCI Test Failure",
    14: "High Voltage",
}

NORMAL_SUBSTATES: Final[Dict[SubState, str]] = {
    0: "No Limits",
    1: "Limited by User",
    2: "Energy Limit",
    3: "Time Limit",
    4: "Cost Limit",
    5: "Schedule 1 Limit",
    6: "Schedule 1 Energy Limit",
    7: "Schedule 2 Limit",
    8: "Schedule 2 Energy Limit",
    9: "Waiting for Activation",
    10: "Paused by Adaptive Mode",
}


def get_charging_state(state_value: int) -> str:
    """Get charging state mapping."""
    return CHARGING_STATES.get(state_value, "Unknown")


def get_error_state(state_value: int) -> str:
    """Get error state mapping."""
    return ERROR_STATES.get(state_value, "Unknown Error")


def get_normal_substate(state_value: int) -> str:
    """Get normal substate mapping."""
    return NORMAL_SUBSTATES.get(state_value, "Unknown State")
