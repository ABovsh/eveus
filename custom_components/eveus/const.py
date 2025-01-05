"""Constants for the Eveus integration."""
from datetime import timedelta
import logging

DOMAIN = "eveus"
SCAN_INTERVAL = timedelta(seconds=30)  # Reduced from 60 to 30 seconds for more frequent updates

# Configuration
DEFAULT_HOST = "192.168.3.39"  # Default IP address
DEFAULT_USERNAME = "admin"      # Default username

# Logger
LOGGER = logging.getLogger(__name__)

# State Classes
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

# Sub-States
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
