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

import asyncio
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
        # A monotonically increasing "attempt epoch". Each re-arm bumps it so an
        # in-flight _stop() spawned in an older epoch (the switch was toggled or
        # the session changed while its Stop command was awaiting) can neither
        # touch the latch nor emit a duplicate event.
        self._generation = 0
        self._stop_task: asyncio.Task | None = None

    def set_enabled(self, enabled: bool) -> None:
        """Enable/disable enforcement; re-arm only on an actual change.

        Idempotent: a redundant enable (e.g. an automation re-asserting the
        switch on every tick) must not re-arm and re-stop a session already
        limited.
        """
        if enabled == self._enabled:
            return
        self._enabled = enabled
        self._rearm()

    def _rearm(self) -> None:
        """Reset the latch and invalidate (and cancel) any in-flight stop."""
        self._fired = False
        self._generation += 1
        if self._stop_task is not None and not self._stop_task.done():
            self._stop_task.cancel()
        self._stop_task = None

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
            # Session over: re-arm for the next one (and drop any stale attempt).
            if self._fired or self._stop_task is not None:
                self._rearm()
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
        # At/above target: hand off to _stop(). Latch _fired now so the next
        # poll can't spawn a duplicate stop while this one is in flight; _stop
        # clears the latch again only if it still owns the current epoch and the
        # command did not actually succeed.
        self._fired = True
        generation = self._generation
        self._stop_task = self._hass.async_create_task(
            self._stop(generation, round(current), round(target))
        )

    async def _stop(self, generation: int, soc: int, target: int) -> None:
        """Send the existing Stop Charging command; notify only on success.

        Generation-guarded: if the limit was toggled or the session changed
        while the command was awaiting, this attempt has been superseded — it
        leaves the latch and fires no event. On a genuine failure within the
        current epoch the latch is released so the next poll retries; the
        ``eveus_soc_limit_reached`` event means the charge was actually stopped,
        not merely that the target was computed.
        """
        try:
            stopped = await self._updater.send_command("evseEnabled", 0)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - retry on any command failure
            stopped = False
            _LOGGER.debug("SOC-limit Stop failed (%s)", type(err).__name__)
        if generation != self._generation:
            # Superseded by a re-arm; do not disturb the current epoch.
            return
        if not stopped:
            self._fired = False
            _LOGGER.debug("SOC-limit Stop not accepted; will retry")
            return
        self._hass.bus.async_fire(
            EVENT_SOC_LIMIT_REACHED,
            {
                "device_number": getattr(self._updater, "device_number", 1),
                "soc": soc,
                "target_soc": target,
            },
        )
        _LOGGER.debug("SOC limit reached (%s%% >= %s%%): sent Stop", soc, target)
