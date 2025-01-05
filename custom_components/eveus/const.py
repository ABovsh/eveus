"""Constants for the Eveus integration."""
from datetime import timedelta
import logging

DOMAIN = "eveus"
SCAN_INTERVAL = timedelta(seconds=30)
LOGGER = logging.getLogger(__package__)
LOGGER.setLevel(logging.DEBUG)
