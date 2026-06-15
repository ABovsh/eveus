"""Integration-enforced SOC limit: stop charging at Target SOC.

A coordinator listener. The charger has no knowledge of the car's SOC, so this
is the one limit Home Assistant must enforce itself. It performs the stop by
reusing the existing Stop Charging command (``evseEnabled=0``); it adds no new
stop mechanism. On firing it also emits the ``eveus_soc_limit_reached`` event so
the user can route a notification (Telegram, mobile) with their own automation —
the integration deliberately does not send notifications itself. Fires once per
charging session and re-arms when the session ends. Skips failed/unavailable
polls so stale data can't trip it.
"""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from .const import MAX_ENERGY_KWH, SESSION_ACTIVE_STATES
from .utils import get_safe_value

_LOGGER = logging.getLogger(__name__)

# Fired on the bus each time the SOC limit stops a charge. Payload:
# {"device_number": int, "soc": int, "target_soc": int}. Report-only — the user
# decides how (or whether) to notify.
EVENT_SOC_LIMIT_REACHED = "eveus_soc_limit_reached"


class SocLimitController:
    """Stop charging at Target SOC by reusing the Stop Charging command."""

    def __init__(self, hass: HomeAssistant, updater, soc_calculator) -> None:
        self._hass = hass
        self._updater = updater
        self._calc = soc_calculator
        self._enabled = False
        self._fired = False

    def set_enabled(self, enabled: bool) -> None:
        """Enable/disable enforcement; re-arm on every toggle."""
        self._enabled = enabled
        self._fired = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def process(self) -> None:
        """Evaluate the latest successful poll and fire the stop if due."""
        if (
            not self._enabled
            or not self._updater.available
            or not self._updater.last_update_success
            or not isinstance(self._updater.data, dict)
        ):
            return
        data = self._updater.data
        state = get_safe_value(data, "state", int)
        if state not in SESSION_ACTIVE_STATES:
            # Not in an active session: the current session is over, so re-arm
            # for the next one.
            self._fired = False
            return
        if self._fired:
            return
        target = self._calc.target_soc
        if target is None:
            return
        energy = get_safe_value(data, "sessionEnergy", float)
        if energy is None or not 0 <= energy <= MAX_ENERGY_KWH:
            return
        current = self._calc.get_soc_percent(energy)
        if current is None or current < target:
            return
        # At/above target: fire the existing Stop Charging command once, then
        # emit an event the user can turn into a notification.
        self._fired = True
        self._hass.async_create_task(self._updater.send_command("evseEnabled", 0))
        self._hass.bus.async_fire(
            EVENT_SOC_LIMIT_REACHED,
            {
                "device_number": getattr(self._updater, "device_number", 1),
                "soc": round(current),
                "target_soc": round(target),
            },
        )
        _LOGGER.debug("SOC limit reached (%.0f%% >= %.0f%%): sent Stop", current, target)
