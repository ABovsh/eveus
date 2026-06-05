"""Coordinator-backed network handling for Eveus integration."""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import timedelta
import logging
import time
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .common_command import CommandManager
from .const import (
    CHARGING_STATES,
    CHARGING_UPDATE_INTERVAL,
    DEFAULT_SCHEME,
    ERROR_LOG_RATE_LIMIT,
    IDLE_UPDATE_INTERVAL,
    OFFLINE_UPDATE_INTERVAL,
    RETRY_DELAY,
    SESSION_ACTIVE_STATES,
    UPDATE_TIMEOUT,
)
from ._payload import PayloadError, validate_main_payload
from .utils import RateLog

_UPDATE_TIMEOUT_OBJ: aiohttp.ClientTimeout = aiohttp.ClientTimeout(total=UPDATE_TIMEOUT)

# Sequence of refreshes after a successful command. Covers both fast
# commits (e.g. Charging Current — applied immediately, visible at 3 s)
# and slow state transitions (Stop Charging off + One Charge on — the
# contactor typically closes 5-15 s after the command, so a single early
# poll catches the device still in Standby/Connected and the coordinator
# then reverts to the 60 s idle cadence, hiding the real transition for
# almost a minute).
POST_COMMAND_REFRESH_DELAYS: tuple[int, ...] = (3, 10, 20)

_LOGGER = logging.getLogger(__name__)
_CHARGING_INTERVAL = timedelta(seconds=CHARGING_UPDATE_INTERVAL)
_IDLE_INTERVAL = timedelta(seconds=IDLE_UPDATE_INTERVAL)
_OFFLINE_INTERVAL = timedelta(seconds=OFFLINE_UPDATE_INTERVAL)
_UPDATE_INTERVALS = {
    CHARGING_UPDATE_INTERVAL: _CHARGING_INTERVAL,
    IDLE_UPDATE_INTERVAL: _IDLE_INTERVAL,
    OFFLINE_UPDATE_INTERVAL: _OFFLINE_INTERVAL,
}

# Longest the offline backoff ever defers the next poll. Used both to set the
# deadline and to bound the skip check, so a backward wall-clock step (which
# would otherwise leave the deadline far in the future) can't strand the
# charger as unavailable: a remaining wait beyond this means the clock moved.
_MAX_OFFLINE_BACKOFF = min(RETRY_DELAY * 4, 300)


class EveusUpdater(DataUpdateCoordinator[dict[str, Any]]):
    """Data coordinator for an Eveus charger."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        hass: HomeAssistant,
        scheme: str = DEFAULT_SCHEME,
        config_entry: ConfigEntry | None = None,
        device_number: int | None = None,
    ) -> None:
        """Initialize updater."""
        # HA logs the coordinator name at ERROR/INFO level on poll failures, so
        # it must stay host-free to honor the host-redaction-in-logs guarantee.
        # The device number keeps multi-charger logs distinguishable instead.
        coordinator_name = (
            f"Eveus EV Charger {device_number}"
            if device_number is not None
            else "Eveus EV Charger"
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=coordinator_name,
            update_interval=_CHARGING_INTERVAL,
        )
        self.host = host
        self.scheme = scheme
        self._basic_auth = aiohttp.BasicAuth(username, password)
        self._command_manager = CommandManager(self)

        self._poll_results: deque[bool] = deque(maxlen=20)
        self._consecutive_failures = 0
        # 0.0 until the first successful poll, so connection_quality does not
        # report "healthy" before the charger has ever answered.
        self._last_success_time = 0.0
        self._latency_samples: deque[float] = deque(maxlen=10)
        self._connection_quality_cache: dict[str, Any] | None = None

        self._availability_log = RateLog()
        self._silent_mode = False
        self._offline_announced = False
        self._last_error: str | None = None
        self._device_available = True
        self._device_registry_finalized = False
        self._next_poll_attempt = 0.0
        self._force_refresh_requests = 0
        self._pending_refresh_unsubs: list = []
        self._post_command_refresh_tasks: list = []
        # Set once async_shutdown runs (entry unload / HA stop). Blocks a command
        # that completes mid-unload from scheduling fresh refresh timers, and a
        # just-fired timer from starting a refresh on a torn-down coordinator.
        self._shutting_down = False

    @property
    def basic_auth(self) -> aiohttp.BasicAuth:
        """Cached Basic Auth credentials for charger requests."""
        return self._basic_auth

    @property
    def available(self) -> bool:
        """Return whether the most recent poll succeeded.

        Kept in sync with `last_update_success` via `_record_success` /
        `_record_failure` so the entity layer and HA's coordinator framework
        agree on reachability. Entities layer their own grace period on top
        of this in `BaseEveusEntity`.
        """
        return self._device_available

    @property
    def connection_quality(self) -> dict[str, Any]:
        """Connection metrics exposed for diagnostics and sensors."""
        if self._connection_quality_cache is not None:
            return self._connection_quality_cache

        if self._poll_results:
            success_rate = (sum(self._poll_results) / len(self._poll_results)) * 100
        else:
            success_rate = 100.0
        avg_latency = (
            (sum(self._latency_samples) / len(self._latency_samples))
            if self._latency_samples
            else 0.0
        )
        self._connection_quality_cache = {
            "success_rate": success_rate,
            "latency_avg": avg_latency,
            "consecutive_failures": self._consecutive_failures,
            "consecutive_command_failures": self._command_manager.consecutive_failures,
            "is_healthy": (
                self._last_success_time > 0
                and success_rate > 80
                and time.time() - self._last_success_time < 300
            ),
            "last_success_time": self._last_success_time,
            "last_error": self._last_error,
            "sample_count": len(self._poll_results),
        }
        return self._connection_quality_cache

    @property
    def is_likely_offline(self) -> bool:
        """Check if the device appears to be powered off."""
        return self._consecutive_failures > 10 and time.time() - self._last_success_time > 600

    def get_session(self) -> aiohttp.ClientSession:
        """Get Home Assistant shared HTTP session."""
        return async_get_clientsession(self.hass)

    def url_for(self, path: str) -> str:
        """Build a charger URL with the configured transport."""
        return f"{self.scheme}://{self.host}{path}"

    async def send_command(
        self,
        command: str,
        value: Any,
        *,
        retry: bool = True,
        extra: dict[str, Any] | None = None,
    ) -> bool:
        """Send command to the device and schedule a delayed refresh on success."""
        success = await self._command_manager.send_command(
            command, value, retry=retry, extra=extra
        )
        self._connection_quality_cache = None
        if success and not self._shutting_down:
            self._schedule_post_command_refresh()
        return success

    async def async_force_refresh(self) -> None:
        """Force an immediate refresh, bypassing offline-backoff skips.

        The bypass is scoped to this call via a counter rather than a single
        consumable flag, so a scheduled poll that interleaves with the forced
        refresh cannot steal the bypass and leave the forced poll skipped.
        """
        self._force_refresh_requests += 1
        try:
            await self.async_refresh()
        finally:
            self._force_refresh_requests -= 1

    def _schedule_post_command_refresh(self) -> None:
        """Schedule delayed refreshes after a successful command.

        Refreshes fire at POST_COMMAND_REFRESH_DELAYS to catch both fast
        commits (e.g. setting current) and slower transitions (Stop Charging
        off + One Charge on, where the contactor may take 5-15 s to close).
        A single early poll is not enough: it sees the device still idle and
        the coordinator reverts to the 60 s idle cadence, so the real
        CHARGING state is hidden for almost a minute. Multiple polls bracket
        the transition window so the coordinator snaps to the 30 s charging
        cadence as soon as the device actually transitions.

        Rapid toggles cancel ALL pending refreshes and reschedule, so refreshes
        always fire relative to the most recent command. A timer that has not
        fired yet is cancelled via its async_call_later unsub; a refresh that
        has already fired and is still in flight is run as a tracked task so it
        too can be cancelled on reschedule or shutdown -- otherwise a slow /main
        poll could complete after a newer command and publish stale data, or
        outlive async_shutdown. Combined with the entity-level optimistic state
        TTL this prevents stale-read flicker.
        """
        self._cancel_pending_refreshes()
        for delay in POST_COMMAND_REFRESH_DELAYS:
            async def _run(_now, _delay=delay):
                if self._shutting_down or self.hass is None or self.hass.is_stopping:
                    return
                task = asyncio.ensure_future(self.async_refresh())
                self._post_command_refresh_tasks.append(task)
                try:
                    await task
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("Post-command refresh failed", exc_info=True)
                finally:
                    if task in self._post_command_refresh_tasks:
                        self._post_command_refresh_tasks.remove(task)

            self._pending_refresh_unsubs.append(async_call_later(self.hass, delay, _run))

    def _pop_pending_refreshes(self) -> list:
        pending, self._pending_refresh_unsubs = self._pending_refresh_unsubs, []
        return pending

    def _cancel_pending_refreshes(self) -> None:
        for unsub in self._pop_pending_refreshes():
            unsub()
        # task.cancel() schedules each task's done-callback via the event loop,
        # so _post_command_refresh_tasks is not mutated during this loop —
        # iterate it directly rather than over a throwaway snapshot.
        for task in self._post_command_refresh_tasks:
            if not task.done():
                task.cancel()

    async def async_shutdown(self) -> None:
        """Cancel any pending delayed refreshes and shut down."""
        self._shutting_down = True
        self._cancel_pending_refreshes()
        pending = list(self._post_command_refresh_tasks)
        self._post_command_refresh_tasks.clear()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await super().async_shutdown()

    def _should_log(self) -> bool:
        """Rate-limit availability logging."""
        return self._availability_log.should_log(ERROR_LOG_RATE_LIMIT)

    def _record_success(self, response_time: float, new_data: dict[str, Any]) -> None:
        """Record a successful poll and tune the next interval."""
        self._connection_quality_cache = None
        was_likely_offline = self.is_likely_offline
        self._poll_results.append(True)
        self._consecutive_failures = 0
        self._device_available = True
        self._next_poll_attempt = 0.0
        self._last_success_time = time.time()
        self._latency_samples.append(response_time)
        self._silent_mode = False
        self._offline_announced = False
        self._last_error = None
        self._tune_update_interval(new_data, preserve_offline=was_likely_offline)

    def _record_failure(self, error: Exception) -> None:
        """Record a failed poll and tune retry cadence."""
        self._connection_quality_cache = None
        self._poll_results.append(False)
        self._consecutive_failures += 1
        self._device_available = False
        self._last_error = "ValueError" if isinstance(error, PayloadError) else type(error).__name__

        if self._consecutive_failures > 20:
            self._silent_mode = True

        if self.is_likely_offline:
            self._next_poll_attempt = time.time() + _MAX_OFFLINE_BACKOFF
            self._set_update_interval(OFFLINE_UPDATE_INTERVAL)
            if not self._offline_announced:
                _LOGGER.debug("Eveus device appears offline, reducing poll frequency")
                self._offline_announced = True

        if not self._silent_mode and self._should_log():
            _LOGGER.debug("Eveus connection issue: %s", type(error).__name__)

    def _tune_update_interval(
        self, data: dict[str, Any], *, preserve_offline: bool = False
    ) -> None:
        """Pick a poll cadence based on charger activity.

        An active session (Charging, or briefly Paused mid-session) is where
        users want fast feedback, so both get CHARGING_UPDATE_INTERVAL. When the
        charger is merely idle/connected we relax to IDLE_UPDATE_INTERVAL to
        halve background HTTP load, and snap back the moment a session resumes.

        ``preserve_offline`` keeps the long offline cadence for the first
        successful poll right after a long outage, so the coordinator does
        not snap straight back to a 30s cycle on a single recovered tick.
        """
        if preserve_offline or self.is_likely_offline:
            self._set_update_interval(OFFLINE_UPDATE_INTERVAL)
            return

        try:
            state_value = int(data.get("state")) if data.get("state") is not None else None
        except (TypeError, ValueError):
            state_value = None

        if state_value in SESSION_ACTIVE_STATES:
            self._set_update_interval(CHARGING_UPDATE_INTERVAL)
        elif state_value is not None and state_value in CHARGING_STATES:
            self._set_update_interval(IDLE_UPDATE_INTERVAL)
        else:
            # Unknown/invalid state: hold offline cadence rather than snap to
            # idle polling. The payload validator at /main rejects the response,
            # so this branch only matters for transient between-tick recovery.
            self._set_update_interval(OFFLINE_UPDATE_INTERVAL)

    def _set_update_interval(self, seconds: int) -> None:
        """Apply a new poll interval if it differs from the current one."""
        new_interval = _UPDATE_INTERVALS.get(seconds)
        if new_interval is None:
            new_interval = timedelta(seconds=seconds)
        if self.update_interval != new_interval:
            self.update_interval = new_interval

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch current device data."""
        start_time = time.time()
        bypass_backoff = self._force_refresh_requests > 0
        backoff_remaining = self._next_poll_attempt - start_time
        if 0 < backoff_remaining <= _MAX_OFFLINE_BACKOFF and not bypass_backoff:
            raise UpdateFailed("Skipping Eveus poll during offline backoff")

        try:
            async with self.get_session().post(
                self.url_for("/main"),
                auth=self._basic_auth,
                timeout=_UPDATE_TIMEOUT_OBJ,
            ) as response:
                if response.status == 401:
                    self._record_failure(ConfigEntryAuthFailed("401"))
                    raise ConfigEntryAuthFailed("Invalid authentication")
                response.raise_for_status()

                new_data = await response.json(content_type=None)
                # Shared validator retains the historical common-network guards:
                # "Eveus 'state' field is boolean" / "Eveus 'state' field is not finite".
                new_data = validate_main_payload(new_data)

                self._record_success(time.time() - start_time, new_data)
                return new_data

        except ConfigEntryAuthFailed:
            raise
        except ValueError as err:
            self._record_failure(err)
            raise UpdateFailed(f"Invalid Eveus response: {type(err).__name__}") from err
        except (
            aiohttp.ClientResponseError,
            aiohttp.ClientConnectorError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as err:
            self._record_failure(err)
            raise UpdateFailed(f"Eveus connection issue: {type(err).__name__}") from err
