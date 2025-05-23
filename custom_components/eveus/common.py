"""Common functionality for Eveus integration."""
from homeassistant.exceptions import HomeAssistantError

from .common_base import BaseEveusEntity, EveusSensorBase, EveusDiagnosticSensor
from .common_network import EveusUpdater
from .common_command import send_eveus_command, CommandManager

class EveusError(HomeAssistantError):
    """Base class for Eveus errors."""


class EveusConnectionError(EveusError):
    """Error indicating connection issues."""


class EveusResponseError(EveusError):
    """Error indicating invalid response."""


__all__ = [
    'BaseEveusEntity',
    'EveusSensorBase',
    'EveusDiagnosticSensor',
    'EveusUpdater',
    'CommandManager',
    'EveusError',
    'EveusConnectionError',
    'EveusResponseError',
    'send_eveus_command',
]
