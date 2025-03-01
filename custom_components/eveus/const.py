"""Constants for the Eveus integration."""
from datetime import timedelta
from typing import Final, Dict, List, TypedDict, Literal

DOMAIN: Final[str] = "eveus"

# Update intervals
SCAN_INTERVAL: Final[timedelta] = timedelta(seconds=30)
CHARGING_UPDATE_INTERVAL: Final[int] = 30  # 30 seconds when charging
IDLE_UPDATE_INTERVAL: Final[int] = 60      # 60 seconds when idle
RETRY_DELAY: Final[int] = 15
UPDATE_TIMEOUT: Final[int] = 20
COMMAND_TIMEOUT: Final[int] = 25
ERROR_COOLDOWN: Final[int] = 300  # 5 minutes

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

# Rate States
RATE_STATES: Final[Dict[int, str]] = {
    0: "Primary Rate",
    1: "Rate 2",
    2: "Rate 3"
}

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
    tarif: float
    activeTarif: int
    tarifAEnable: int
    tarifBEnable: int
    tarifAValue: float
    tarifBValue: float
    tarifAStart: int
    tarifAStop: int
    tarifBStart: int
    tarifBStop: int

# Rate Commands
RATE_COMMANDS = {
    "RATE2_ENABLE": "tarifAEnable",
    "RATE3_ENABLE": "tarifBEnable",
    "PRIMARY_RATE": "tarif",
    "RATE2_START": "tarifAStart",
    "RATE2_STOP": "tarifAStop",
    "RATE3_START": "tarifBStart",
    "RATE3_STOP": "tarifBStop",
}

# Command Endpoints
CMD_ENDPOINT = "pageEvent"

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
