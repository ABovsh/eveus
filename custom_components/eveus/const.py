"""Constants for the Eveus integration."""
from datetime import timedelta
import logging

DOMAIN = "eveus"
SCAN_INTERVAL = timedelta(seconds=60)

# Logger
LOGGER = logging.getLogger(__package__)
