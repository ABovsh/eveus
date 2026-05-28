"""Coordinator-backed network handling for Eveus integration."""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import timedelta
import logging
import math
import time
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .common_command import CommandManager
from .const import (
    CHARGING_STATES,
    CHARGING_UPDATE_INTERVAL,
    DEFAULT_SCHEME,
    DEVICE_STATE_CHARGING,
    ERROR_LOG_RATE_LIMIT,
    IDLE_UPDATE_INTERVAL,
    OFFLINE_UPDATE_INTERVAL,
    RETRY_DELAY,
    UPDATE_TIMEOUT,
)
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
    ) -> None:
        """Initialize updater."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"Eveus EV Charger {host}",
            update_interval=_CHARGING_INTERVAL,
        )
        self.host = host
        self.scheme = scheme
        self._basic_auth = aiohttp.BasicAuth(username, password)
        self._command_manager = CommandManager(self)

        self._poll_results: deque[bool] = deque(maxlen=20)
        self._consecutive_failures = 0
        self._last_success_time = time.time()
        self._latency_samples: deque[float] = deque(maxlen=10)

        self._availability_log = RateLog()
        self._silent_mode = False
        self._offline_announced = False
        self._last_error: str | None = None
        self._device_available = True
        self._next_poll_attempt = 0.0
        self._force_refresh_requested = False
        self._post_command_refresh_tasks: list[asyncio.Task] = []

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
        if self._poll_results:
            success_rate = (sum(self._poll_results) / len(self._poll_results)) * 100
        else:
            success_rate = 100.0
        avg_latency = (
            (sum(self._latency_samples) / len(self._latency_samples))
            if self._latency_samples
            else 0.0
        )
        return {
            "success_rate": success_rate,
            "latency_avg": avg_latency,
            "consecutive_failures": self._consecutive_failures,
            "consecutive_command_failures": self._command_manager.consecutive_failures,
            "is_healthy": success_rate > 80 and time.time() - self._last_success_time < 300,
            "last_success_time": self._last_success_time,
            "last_error": self._last_error,
            "sample_count": len(self._poll_results),
        }

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
    ) -> bool:
        """Send command to the device and schedule a delayed refresh on success."""
        success = await self._command_manager.send_command(command, value, retry=retry)
        if success:
            self._schedule_post_command_refresh()
        return success

    async def async_force_refresh(self) -> None:
        """Force an immediate refresh, bypassing one offline-backoff skip."""
        self._force_refresh_requested = True
        try:
            await self.async_refresh()
        finally:
            self._force_refresh_requested = False

    async def _delayed_refresh(self, delay: float) -> None:
        """Wait `delay` seconds then run an immediate, non-debounced refresh.

        async_refresh (not async_request_refresh) is used because the latter
        is debounced ~10s inside HA's coordinator and would defeat the whole
        point of scheduling a quick post-command refresh.
        """
        await asyncio.sleep(delay)
        if self.hass is None or self.hass.is_stopping:
            return
        try:
            await self.async_refresh()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Post-command refresh failed", exc_info=True)

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
        always fire relative to the most recent command. Combined with the
        entity-level optimistic state TTL this prevents stale-read flicker.
        """
        self._cancel_pending_refreshes()
        for delay in POST_COMMAND_REFRESH_DELAYS:
            task = self.hass.async_create_task(self._delayed_refresh(delay))
            self._post_command_refresh_tasks.append(task)

    def _cancel_pending_refreshes(self) -> None:
        """Cancel any pending post-command refresh tasks."""
        for task in self._post_command_refresh_tasks:
            if not task.done():
                task.cancel()
        self._post_command_refresh_tasks.clear()

    async def async_shutdown(self) -> None:
        """Cancel any pending delayed refreshes and shut down."""
        pending = list(self._post_command_refresh_tasks)
        for task in pending:
            if not task.done():
                task.cancel()
        self._post_command_refresh_tasks.clear()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await super().async_shutdown()

    def _should_log(self) -> bool:
        """Rate-limit availability logging."""
        return self._availability_log.should_log(ERROR_LOG_RATE_LIMIT)

    def _record_success(self, response_time: float, new_data: dict[str, Any]) -> None:
        """Record a successful poll and tune the next interval."""
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
        self._poll_results.append(False)
        self._consecutive_failures += 1
        self._device_available = False
        self._last_error = type(error).__name__

        if self._consecutive_failures > 20:
            self._silent_mode = True

        if self.is_likely_offline:
            self._next_poll_attempt = time.time() + min(RETRY_DELAY * 4, 300)
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

        Charging is the only state where users want fast feedback. When the
        charger is idle/connected we relax to IDLE_UPDATE_INTERVAL to halve
        background HTTP load, and snap back to CHARGING_UPDATE_INTERVAL the
        moment the device starts a session.

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

        if state_value == DEVICE_STATE_CHARGING:
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
        bypass_backoff = self._force_refresh_requested
        self._force_refresh_requested = False
        if self._next_poll_attempt > start_time and not bypass_backoff:
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
                if not isinstance(new_data, dict):
                    raise ValueError(f"Expected dict, got {type(new_data).__name__}")
                if "state" not in new_data:
                    raise ValueError("Response missing required Eveus 'state' field")
                # Match the config-flow contract: a genuine Eveus /main payload
                # always carries currentSet. Requiring it here too rejects a
                # misrouted host that happens to return a plausible bare state.
                if "currentSet" not in new_data:
                    raise ValueError("Response missing required Eveus 'currentSet' field")
                raw_state = new_data["state"]
                if isinstance(raw_state, bool):
                    raise ValueError("Eveus 'state' field is boolean")
                if isinstance(raw_state, float) and not math.isfinite(raw_state):
                    raise ValueError("Eveus 'state' field is not finite")
                if isinstance(raw_state, float) and not raw_state.is_integer():
                    raise ValueError("Eveus 'state' field is not an integer")
                try:
                    state_value = int(raw_state)
                except (TypeError, ValueError, OverflowError) as err:
                    raise ValueError("Eveus 'state' field is not numeric") from err
                if state_value not in CHARGING_STATES:
                    raise ValueError(f"Eveus 'state' value {state_value} outside known domain")

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
