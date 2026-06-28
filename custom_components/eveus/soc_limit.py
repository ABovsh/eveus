"""Integration-enforced SOC limit: stop charging at Target SOC.

A coordinator listener. The charger has no knowledge of the car's SOC, so this
is the one limit Home Assistant must enforce itself. It performs the stop by
reusing the existing Stop Charging command (``evseEnabled=1``); it adds no new
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
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import MAX_ENERGY_KWH, SESSION_ACTIVE_STATES
from .utils import get_safe_value

_LOGGER = logging.getLogger(__name__)

# Fired on the bus each time the SOC limit stops a charge. Payload:
# {"device_number": int, "soc": int, "target_soc": int}. Report-only — the user
# decides how (or whether) to notify.
EVENT_SOC_LIMIT_REACHED = "eveus_soc_limit_reached"

# A drop in ``sessionEnergy`` larger than this (kWh) means the session counter
# reset — i.e. a new charging session — rather than in-session jitter.
_SESSION_RESET_EPS_KWH = 0.5


class SocLimitController:
    """Stop charging at Target SOC by reusing the Stop Charging command."""

    def __init__(self, hass: HomeAssistant, updater, soc_calculator) -> None:
        self._hass = hass
        self._updater = updater
        self._calc = soc_calculator
        self._enabled = False
        # ``_fired`` latches once the stop is CONFIRMED — the charger itself
        # reports ``evseEnabled == 1`` — not merely once the command POST
        # returned 2xx. (Firmware polarity: ``evseEnabled`` 0 = charging, 1 =
        # stopped; the Stop command sends 1, matching the Stop Charging switch.)
        self._fired = False
        # Set (soc, target) once a Stop POST is accepted; the attempt then awaits
        # the charger reporting ``evseEnabled == 1``. Confirmation is attributable
        # to our command (an unrelated unplug leaves ``evseEnabled == 0``), and is
        # held across retries so an end-of-session poll can't lose it. While it is
        # set and the charger is still charging, each active poll re-sends the Stop.
        self._pending: tuple[int, int] | None = None
        # ``sessionEnergy`` observed when the pending Stop was issued. It only
        # grows within a session and resets at a new one, so a later poll showing
        # a smaller value means the boundary was missed (e.g. hidden by failed
        # polls) — the token belongs to the old session and must be discarded
        # before it can be falsely confirmed in the new one.
        self._pending_energy: float | None = None
        # ``sessionTime`` observed when the pending Stop was issued. Like
        # ``sessionEnergy`` it only grows within a session and resets at a new
        # one, but it is independent of how much energy was delivered — so a new
        # session that happens to drop ``sessionEnergy`` by less than the reset
        # epsilon (e.g. 0.4 -> 0.1 kWh) is still caught by its clock resetting.
        self._pending_session_time: float | None = None
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

    async def async_shutdown(self) -> None:
        """Stop enforcing and cancel any in-flight Stop before the entry unloads.

        Registered via ``entry.async_on_unload`` so a Stop command queued/awaiting
        when the config entry reloads or is removed cannot still POST to the
        charger after this integration instance is gone (``hass.async_create_task``
        is not bound to the entry lifecycle by Home Assistant).
        """
        self._enabled = False
        task = self._stop_task
        self._rearm()  # bumps the generation and cancels the in-flight task
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    def _rearm(self) -> None:
        """Reset the latch and invalidate (and cancel) any in-flight stop."""
        self._fired = False
        self._pending = None
        self._pending_energy = None
        self._pending_session_time = None
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
        # The charger's master switch ("Disable limits", ``suspendLimits``)
        # overrides every limit, including this HA-enforced one; stand down before
        # confirming or issuing a Stop. This mirrors how the charger treats its
        # own Time/Energy/Cost limits, verified live on R3.05.2:
        #   suspendLimits 0->1  -> the charger clears each native enable flag
        #                          (timeLimitS/energyLimitS/moneyLimitS -> 0), and
        #                          the SOC switch follows to off on the same edge.
        #   while suspended     -> a limit (incl. this SOC switch) CAN be enabled
        #                          again but has no effect on the session.
        #   suspendLimits 1->0  -> whatever limits are enabled become active again;
        #                          no auto-enable of a limit you left off.
        # Require a clean 0 to enforce: a missing/malformed/out-of-domain
        # suspendLimits means the master state is unknown, and the conservative
        # safety contract is to stand down rather than risk stopping while the
        # master may actually be active.
        if get_safe_value(data, "suspendLimits", int) != 0:
            return
        state = get_safe_value(data, "state", int)
        evse_enabled = get_safe_value(data, "evseEnabled", int)
        energy = get_safe_value(data, "sessionEnergy", float)
        session_time = get_safe_value(data, "sessionTime", int)
        # Session-identity guard for an UNCONFIRMED attempt whose boundary was
        # HIDDEN by failed polls: a pending token belongs to the session whose
        # counters we recorded. Both ``sessionEnergy`` and ``sessionTime`` only
        # grow within a session and reset at a new one, so a later ACTIVE poll
        # reporting a smaller value for EITHER proves a NEW session is running —
        # discard the stale token before an unrelated 0 there confirms it. The
        # clock check catches a new session whose energy dropped by less than the
        # kWh epsilon. Gated on an active state so the current session's own end
        # still confirms normally below.
        #
        # Deliberately NOT extended to a CONFIRMED stop (``_fired``): once the
        # limit has fired, it stays latched until a session boundary is actually
        # OBSERVED (an inactive poll re-arms it below) — matching how the charger
        # treats its own limits and how Anton uses this rare backup (his car
        # already self-limits SOC). A session that begins entirely between polls
        # is intentionally skipped rather than stopped, so the limit never
        # re-fires off a boundary it never saw.
        if state in SESSION_ACTIVE_STATES and self._pending is not None:
            energy_reset = (
                self._pending_energy is not None
                and energy is not None
                and energy < self._pending_energy - _SESSION_RESET_EPS_KWH
            )
            time_reset = (
                self._pending_session_time is not None
                and session_time is not None
                and session_time < self._pending_session_time
            )
            if energy_reset or time_reset:
                self._rearm()
        # Attributable, causal confirmation: we issue Stop only while the charger
        # reports ``evseEnabled == 0`` (below), so a later 1 IS the 0->1 transition
        # our command caused — not a pre-existing/stale 1 and not an unplug (which
        # leaves it 0). Checked before the session-over return so a stop that ends
        # the session in the same poll is still confirmed.
        if self._pending is not None and not self._fired and evse_enabled == 1:
            self._emit_reached(*self._pending)
        if state not in SESSION_ACTIVE_STATES:
            # Session boundary — derived from ``state`` alone, so a payload missing
            # the optional ``evseEnabled`` still re-arms here. Confirmation above
            # only fires on a present 1; an unconfirmable attempt is DISCARDED at
            # the boundary rather than carried into the next session (which would
            # let a later unrelated 1 falsely confirm it).
            if self._fired or self._pending is not None or self._stop_task is not None:
                self._rearm()
            return
        # Active session from here. SOC enforcement needs a known charge state.
        if evse_enabled is None:
            # No boundary to handle in an active poll, so skipping loses nothing;
            # ``_pending`` (if any) stays armed for a later complete poll.
            return
        if self._fired:
            return
        if self._stop_task is not None and not self._stop_task.done():
            # A Stop is mid-flight; wait for it before deciding anything.
            return
        if evse_enabled != 0:
            # Charging is already stopped — nothing to stop, and issuing now would
            # let a 1 we did NOT cause be misread as our confirmation. Wait until we
            # observe it charging.
            return
        # ``_pending`` is intentionally NOT cleared here: it is the confirmation
        # token and is held across retries. While it is set and the charger is
        # still charging (no evseEnabled==1 yet), we fall through and re-send Stop.
        target = self._calc.target_soc
        if target is None:
            return
        if energy is None or not 0 <= energy <= MAX_ENERGY_KWH:
            return
        # Use the EXACT SOC percent for the target comparison, not the rounded
        # display percent: on a large battery a rounded-up percent reaches the
        # target up to ~0.5% before the battery actually does, stopping early.
        current = self._calc.get_soc_percent_exact(energy)
        if current is None or current < target:
            return
        # At/above target and charging enabled: hand off to _stop(). Record the
        # session's energy so the token can be bound to this session.
        generation = self._generation
        self._stop_task = self._hass.async_create_task(
            self._stop(generation, round(current), round(target), energy, session_time)
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

    async def _stop(
        self,
        generation: int,
        soc: int,
        target: int,
        energy: float,
        session_time: float | None = None,
    ) -> None:
        """Send the existing Stop Charging command; confirm before notifying.

        Generation-guarded: if the limit was toggled or the session changed while
        the command was awaiting, this attempt has been superseded — it touches
        nothing. A successful POST only proves the charger ACCEPTED the request,
        not that charging stopped, so it marks the attempt ``_pending`` rather than
        emitting the event; ``process()`` confirms via the charger reporting
        ``evseEnabled == 1`` (and re-sends each poll until it does). A failed POST
        leaves no pending mark so the next poll simply retries.
        """
        # Record the attempt BEFORE awaiting the command: a poll that completes
        # while the POST is in flight can observe ``evseEnabled == 1`` (the stop
        # already took effect at the charger) and must be able to confirm it —
        # otherwise the boundary re-arm would cancel this task and lose the event.
        # Bound to this session's energy so a missed boundary can't carry it over.
        self._pending = (soc, target)
        self._pending_energy = energy
        self._pending_session_time = session_time
        try:
            # evseEnabled=1 is the Stop command (0 = keep charging); this matches
            # the Stop Charging switch, not the field's misleading name.
            stopped = await self._updater.send_command("evseEnabled", 1)
        except asyncio.CancelledError:
            raise
        except ConfigEntryAuthFailed:
            # The charger rejected our credentials. Unlike an entity service call,
            # this background task can't propagate the 401 to Home Assistant, so
            # the reauth flow would never start and charging could continue past
            # target until a later poll also 401s. Withdraw the provisional token
            # and start reauth explicitly through the config entry.
            if not self._fired and generation == self._generation:
                self._pending = None
                self._pending_energy = None
                self._pending_session_time = None
            entry = getattr(self._updater, "config_entry", None)
            if entry is not None:
                entry.async_start_reauth(self._hass)
            _LOGGER.debug("SOC-limit Stop hit auth failure; started reauth")
            return
        except Exception as err:  # noqa: BLE001 - retry on any command failure
            stopped = False
            _LOGGER.debug("SOC-limit Stop failed (%s)", type(err).__name__)
        if generation != self._generation:
            # Superseded by a re-arm; do not disturb the current epoch.
            return
        if not stopped:
            # POST rejected: withdraw the provisional token (unless a poll already
            # confirmed it while the command was in flight) so a later unrelated 1
            # can't confirm a stop that never happened; the next poll retries.
            if not self._fired:
                self._pending = None
                self._pending_energy = None
                self._pending_session_time = None
            _LOGGER.debug("SOC-limit Stop not accepted; will retry")
            return
        _LOGGER.debug("SOC-limit Stop sent (%s%% >= %s%%); awaiting confirm", soc, target)
