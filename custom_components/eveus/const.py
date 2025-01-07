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
