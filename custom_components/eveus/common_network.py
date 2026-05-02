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
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .common_command import CommandManager
from .const import (
    CHARGING_UPDATE_INTERVAL,
    DEVICE_STATE_CHARGING,
    ERROR_LOG_RATE_LIMIT,
    IDLE_UPDATE_INTERVAL,
    OFFLINE_UPDATE_INTERVAL,
    RETRY_DELAY,
    UPDATE_TIMEOUT,
)
from .utils import RateLog

# Single refresh 5 s after a successful command — long enough for the
# charger to commit any state change (including slow Stop Charging
# transitions) while still giving snappy UI feedback.
POST_COMMAND_REFRESH_DELAYS: tuple[int, ...] = (5,)

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
        self.username = username
        self.password = password
        self._command_manager = CommandManager(self)

        self._success_count = 0
        self._total_count = 0
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

    async def send_command(self, command: str, value: Any) -> bool:
        """Send command to the device and schedule a delayed refresh on success."""
        success = await self._command_manager.send_command(command, value)
        if success:
            self._schedule_post_command_refresh()
        return success

    async def _delayed_refresh(self, delay: float) -> None:
        """Wait `delay` seconds then run an immediate, non-debounced refresh.

        async_refresh (not async_request_refresh) is used because the latter
        is debounced ~10s inside HA's coordinator and would defeat the whole
        point of scheduling a quick post-command refresh.
        """
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        if self.hass is None or self.hass.is_stopping:
            return
        try:
            await self.async_refresh()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Post-command refresh failed", exc_info=True)

    def _schedule_post_command_refresh(self) -> None:
        """Schedule delayed refreshes after a successful command.

        Two refreshes (at POST_COMMAND_REFRESH_DELAYS): the early one catches
        fast-committing changes (e.g. setting current) for snappy UI feedback;
        the later one catches slow transitions (e.g. Stop Charging, where the
        charger may take ~5-10s to drop into Standby).

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
        self._cancel_pending_refreshes()
        await super().async_shutdown()

    def _should_log(self) -> bool:
        """Rate-limit availability logging."""
        return self._availability_log.should_log(ERROR_LOG_RATE_LIMIT)

    def _record_success(self, response_time: float, new_data: dict[str, Any]) -> None:
        """Record a successful poll and tune the next interval."""
        self._success_count += 1
        self._total_count += 1
        self._poll_results.append(True)
        self._consecutive_failures = 0
        self._device_available = True
        self._next_poll_attempt = 0.0
        self._last_success_time = time.time()
        self._latency_samples.append(response_time)
        self._silent_mode = False
        self._offline_announced = False
        self._last_error = None
        self._tune_update_interval(new_data)

    def _record_failure(self, error: Exception) -> None:
        """Record a failed poll and tune retry cadence."""
        self._total_count += 1
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
                _LOGGER.debug("Device %s appears offline, reducing poll frequency", self.host)
                self._offline_announced = True

        if not self._silent_mode and self._should_log():
            _LOGGER.debug("Connection issue with %s: %s", self.host, type(error).__name__)

    def _tune_update_interval(self, data: dict[str, Any]) -> None:
        """Pick a poll cadence based on charger activity.

        Charging is the only state where users want fast feedback. When the
        charger is idle/connected we relax to IDLE_UPDATE_INTERVAL to halve
        background HTTP load, and snap back to CHARGING_UPDATE_INTERVAL the
        moment the device starts a session.
        """
        try:
            state_value = int(data.get("state")) if data.get("state") is not None else None
        except (TypeError, ValueError):
            state_value = None

        if state_value == DEVICE_STATE_CHARGING:
            self._set_update_interval(CHARGING_UPDATE_INTERVAL)
        else:
            self._set_update_interval(IDLE_UPDATE_INTERVAL)

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
        if self._next_poll_attempt > start_time:
            raise UpdateFailed(f"Skipping poll for {self.host} during offline backoff")

        try:
            async with self.get_session().post(
                f"http://{self.host}/main",
                auth=aiohttp.BasicAuth(self.username, self.password),
                timeout=aiohttp.ClientTimeout(total=UPDATE_TIMEOUT),
            ) as response:
                if response.status == 401:
                    raise ConfigEntryAuthFailed("Invalid authentication")
                response.raise_for_status()

                new_data = await response.json(content_type=None)
                if not isinstance(new_data, dict):
                    raise ValueError(f"Expected dict, got {type(new_data).__name__}")

                self._record_success(time.time() - start_time, new_data)
                return new_data

        except ConfigEntryAuthFailed:
            raise
        except ValueError as err:
            self._record_failure(err)
            raise UpdateFailed(f"Invalid response from {self.host}: {err}") from err
        except (
            aiohttp.ClientResponseError,
            aiohttp.ClientConnectorError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ) as err:
            self._record_failure(err)
            raise UpdateFailed(f"Connection issue with {self.host}: {type(err).__name__}") from err
