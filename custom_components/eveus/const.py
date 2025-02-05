"""Constants for the Eveus integration."""
from datetime import timedelta
from typing import Final, Dict, List, TypedDict, Literal

DOMAIN: Final[str] = "eveus"
SCAN_INTERVAL: Final[timedelta] = timedelta(seconds=30)

# Current limits
MIN_CURRENT: Final[int] = 7
MODEL_16A: Final[str] = "16A"
MODEL_32A: Final[str] = "32A"
MODELS: Final[List[str]] = [MODEL_16A, MODEL_32A]

# Model specifications
MODEL_MAX_CURRENT: Final[Dict[str, int]] = {
    MODEL_16A: 16,
    MODEL_32A: 32
}

# Configuration
CONF_MODEL: Final[str] = "model"

# API Attributes
class DeviceAttributes(TypedDict, total=False):
    """Device attributes type definitions."""
    voltMeas1: float
    curMeas1: float
    powerMeas: float
    sessionEnergy: float
    totalEnergy: float
    sessionTime: int
    state: int
    subState: int
    currentSet: int
    evseEnabled: int
    temperature1: float
    temperature2: float
    systemTime: int
    IEM1: float
    IEM2: float
    IEM1_money: float
    IEM2_money: float
    ground: int
    vBat: float

# Attribute Constants
ATTR_VOLTAGE: Final[str] = "voltMeas1"
ATTR_CURRENT: Final[str] = "curMeas1"
ATTR_POWER: Final[str] = "powerMeas"
ATTR_SESSION_ENERGY: Final[str] = "sessionEnergy"
ATTR_TOTAL_ENERGY: Final[str] = "totalEnergy"
ATTR_SESSION_TIME: Final[str] = "sessionTime"
ATTR_STATE: Final[str] = "state"
ATTR_SUBSTATE: Final[str] = "subState"
ATTR_CURRENT_SET: Final[str] = "currentSet"
ATTR_ENABLED: Final[str] = "evseEnabled"
ATTR_TEMPERATURE_BOX: Final[str] = "temperature1"
ATTR_TEMPERATURE_PLUG: Final[str] = "temperature2"
ATTR_SYSTEM_TIME: Final[str] = "systemTime"
ATTR_COUNTER_A_ENERGY: Final[str] = "IEM1"
ATTR_COUNTER_B_ENERGY: Final[str] = "IEM2"
ATTR_COUNTER_A_COST: Final[str] = "IEM1_money"
ATTR_COUNTER_B_COST: Final[str] = "IEM2_money"
ATTR_GROUND: Final[str] = "ground"
ATTR_BATTERY_VOLTAGE: Final[str] = "vBat"

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
    7: "Error"
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
    14: "High Voltage"
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
    10: "Paused by Adaptive Mode"
}
