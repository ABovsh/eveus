"""Constants for the Eveus integration."""
from datetime import timedelta
import logging

DOMAIN = "eveus"
SCAN_INTERVAL = timedelta(seconds=10)  # Shorter interval for testing
LOGGER = logging.getLogger(__package__)
