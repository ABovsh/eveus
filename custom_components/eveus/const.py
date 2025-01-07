# File: custom_components/eveus/const.py

"""Constants for the Eveus integration."""
from datetime import timedelta

DOMAIN = "eveus"
SCAN_INTERVAL = timedelta(seconds=30)

# Model constants
MODEL_16A = "16A"
MODEL_32A = "32A"
MODELS = [MODEL_16A, MODEL_32A]
MODEL_MAX_CURRENT = {
    MODEL_16A: 16,
    MODEL_32A: 32,
}
MIN_CURRENT = 8

# Configuration
CONF_BATTERY_CAPACITY = "battery_capacity"
CONF_MODEL = "model"

# API Attributes
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
ATTR_BATTERY_VOLTAGE = "vBat"

# State Mappings
CHARGING_STATES = {
    0: "Startup",
    1: "System Test",
    2: "Standby",
    3: "Connected",
    4: "Charging",
    5: "Charge Complete",
    6: "Paused",
    7: "Error"
}

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
