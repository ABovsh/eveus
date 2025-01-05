"""Constants for the Eveus integration."""
from datetime import timedelta

DOMAIN = "eveus"
SCAN_INTERVAL = timedelta(seconds=30)

# Default connection settings
DEFAULT_HOST = "192.168.3.39"

# Attributes from API response
ATTR_VOLTAGE = "voltMeas1"
ATTR_CURRENT = "curMeas1"
ATTR_POWER = "powerMeas"
ATTR_SESSION_ENERGY = "sessionEnergy"
ATTR_TOTAL_ENERGY = "totalEnergy"
ATTR_SESSION_TIME = "sessionTime"
ATTR_STATE = "state"
ATTR_SUBSTATE = "subState"
ATTR_CURRENT_SET = "currentSet"
ATTR_ENABLED = "evseEnabled"
ATTR_TEMPERATURE_BOX = "temperature1"
ATTR_TEMPERATURE_PLUG = "temperature2"
ATTR_SYSTEM_TIME = "systemTime"
ATTR_COUNTER_A_ENERGY = "IEM1"
ATTR_COUNTER_B_ENERGY = "IEM2"
ATTR_COUNTER_A_COST = "IEM1_money"
ATTR_COUNTER_B_COST = "IEM2_money"
ATTR_GROUND = "ground"

# State mapping
STATE_STARTUP = "Startup"
STATE_SYSTEM_TEST = "System Test"
STATE_STANDBY = "Standby"
STATE_CONNECTED = "Connected"
STATE_CHARGING = "Charging"
STATE_COMPLETE = "Charge Complete"
STATE_PAUSED = "Paused"
STATE_ERROR = "Error"

CHARGING_STATES = {
    0: STATE_STARTUP,
    1: STATE_SYSTEM_TEST,
    2: STATE_STANDBY,
    3: STATE_CONNECTED,
    4: STATE_CHARGING,
    5: STATE_COMPLETE,
    6: STATE_PAUSED,
    7: STATE_ERROR
}

# Error states mapping
ERROR_STATES = {
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

# Normal substates mapping
NORMAL_SUBSTATES = {
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
