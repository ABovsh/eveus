"""Constants for the Eveus integration."""
from typing import Final, Dict, List, Literal
from functools import lru_cache

DOMAIN: Final[str] = "eveus"

# Update intervals
CHARGING_UPDATE_INTERVAL: Final[int] = 30
IDLE_UPDATE_INTERVAL: Final[int] = 60
OFFLINE_UPDATE_INTERVAL: Final[int] = 300
RETRY_DELAY: Final[int] = 15
UPDATE_TIMEOUT: Final[int] = 20
COMMAND_TIMEOUT: Final[int] = 25

# Charger device-state value that means "actively charging" (CHARGING_STATES[4]).
DEVICE_STATE_CHARGING: Final[int] = 4

# Availability and resilience - optimized for WiFi connections
AVAILABILITY_GRACE_PERIOD: Final[int] = 60
CONTROL_GRACE_PERIOD: Final[int] = 30
ERROR_LOG_RATE_LIMIT: Final[int] = 300
STATE_CACHE_TTL: Final[int] = 60

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

# Configuration
CONF_MODEL: Final[str] = "model"

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


@lru_cache(maxsize=32)
def get_charging_state(state_value: int) -> str:
    """Get cached charging state mapping."""
    return CHARGING_STATES.get(state_value, "Unknown")


@lru_cache(maxsize=32)
def get_error_state(state_value: int) -> str:
    """Get cached error state mapping."""
    return ERROR_STATES.get(state_value, "Unknown Error")


@lru_cache(maxsize=32)
def get_normal_substate(state_value: int) -> str:
    """Get cached normal substate mapping."""
    return NORMAL_SUBSTATES.get(state_value, "Unknown State")
