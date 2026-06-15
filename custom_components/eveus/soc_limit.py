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
        # ``_fired`` latches once the stop is CONFIRMED (the session has actually
        # left the active states), not merely once the command POST returned 2xx.
        self._fired = False
        # Set when a Stop command POST succeeded but the charger is still charging:
        # the stop is "sent, awaiting confirmation". The event is emitted only once
        # the session ends; until then each active poll re-sends the Stop, so a
        # command the firmware/OCPP ignored or overrode keeps being enforced.
        self._pending: tuple[int, int] | None = None
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
        self._pending = None
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
            # Session over. If a Stop was awaiting confirmation, the session
            # ending IS the confirmation the charge stopped — emit the event now.
            if self._pending is not None:
                self._emit_reached(*self._pending)
            # Re-arm for the next session (and drop any stale attempt).
            if self._fired or self._pending is not None or self._stop_task is not None:
                self._rearm()
            return
        if self._fired:
            return
        if self._stop_task is not None and not self._stop_task.done():
            # A Stop is mid-flight; wait for it before deciding anything.
            return
        if self._pending is not None:
            # A Stop POST succeeded yet the charger is STILL charging — the
            # command did not take (ignored/overridden). Clear the pending mark
            # and fall through to re-send it this poll.
            self._pending = None
        target = self._calc.target_soc
        if target is None:
            return
        energy = get_safe_value(data, "sessionEnergy", float)
        if energy is None or not 0 <= energy <= MAX_ENERGY_KWH:
            return
        current = self._calc.get_soc_percent(energy)
        if current is None or current < target:
            return
        # At/above target: hand off to _stop().
        generation = self._generation
        self._stop_task = self._hass.async_create_task(
            self._stop(generation, round(current), round(target))
        )

    def _emit_reached(self, soc: int, target: int) -> None:
        """Latch and fire the reached event exactly once for this session."""
        self._fired = True
        self._pending = None
        self._hass.bus.async_fire(
            EVENT_SOC_LIMIT_REACHED,
            {
                "device_number": getattr(self._updater, "device_number", 1),
                "soc": soc,
                "target_soc": target,
            },
        )
        _LOGGER.debug("SOC limit confirmed stopped (%s%% >= %s%%)", soc, target)

    async def _stop(self, generation: int, soc: int, target: int) -> None:
        """Send the existing Stop Charging command; confirm before notifying.

        Generation-guarded: if the limit was toggled or the session changed while
        the command was awaiting, this attempt has been superseded — it touches
        nothing. A successful POST only proves the charger ACCEPTED the request,
        not that charging stopped, so it marks the attempt ``_pending`` rather than
        emitting the event; ``process()`` confirms via the session leaving the
        active states (and re-sends each poll until it does). A failed POST leaves
        no pending mark so the next poll simply retries.
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
            _LOGGER.debug("SOC-limit Stop not accepted; will retry")
            return
        # POST accepted — await session-end confirmation before emitting.
        self._pending = (soc, target)
        _LOGGER.debug("SOC-limit Stop sent (%s%% >= %s%%); awaiting confirm", soc, target)
