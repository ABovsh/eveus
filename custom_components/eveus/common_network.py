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
    CHARGING_UPDATE_INTERVAL,
    DEVICE_STATE_CHARGING,
    ERROR_LOG_RATE_LIMIT,
    IDLE_UPDATE_INTERVAL,
    OFFLINE_UPDATE_INTERVAL,
    RETRY_DELAY,
    UPDATE_TIMEOUT,
)

# Delays after a successful command. The charger commits state changes
# anywhere from ~1s (current change) to ~10s (Stop Charging transitioning
# the device to Standby). Two refreshes give the best of both: the early
# one catches the common fast case for snappy UI; the later one is the
# safety net so users never have to wait the full poll interval.
POST_COMMAND_REFRESH_DELAYS: tuple[int, ...] = (2, 7)

_LOGGER = logging.getLogger(__name__)


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
            update_interval=timedelta(seconds=CHARGING_UPDATE_INTERVAL),
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

        self._last_availability_log = 0
        self._silent_mode = False
        self._offline_announced = False
        self._last_error: str | None = None
        self._device_available = True
        self._next_poll_attempt = 0.0
        self._post_command_refresh_cancels: list = []

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

    def _schedule_post_command_refresh(self) -> None:
        """Schedule delayed refreshes after a successful command.

        Two refreshes are scheduled (at POST_COMMAND_REFRESH_DELAYS): the early
        one catches fast-committing changes (e.g. setting current) for snappy
        UI feedback; the later one catches slow transitions (e.g. Stop
        Charging, where the charger may take ~5-10s to drop to Standby).

        Rapid toggles cancel ALL pending refreshes and reschedule, so refreshes
        always fire relative to the most recent command. Combined with the
        entity-level optimistic state TTL this prevents stale-read flicker.
        """
        self._cancel_pending_refreshes()

        def _make_fire():
            def _fire(_now) -> None:
                if self.hass is None or self.hass.is_stopping:
                    return
                self.hass.async_create_task(self.async_request_refresh())
            return _fire

        for delay in POST_COMMAND_REFRESH_DELAYS:
            cancel = async_call_later(self.hass, delay, _make_fire())
            self._post_command_refresh_cancels.append(cancel)

    def _cancel_pending_refreshes(self) -> None:
        """Cancel any pending post-command refreshes."""
        for cancel in self._post_command_refresh_cancels:
            try:
                cancel()
            except Exception:  # noqa: BLE001 - cancel callbacks are noexcept by contract
                pass
        self._post_command_refresh_cancels.clear()

    async def async_shutdown(self) -> None:
        """Cancel any pending delayed refreshes and shut down."""
        self._cancel_pending_refreshes()
        await super().async_shutdown()

    def _should_log(self) -> bool:
        """Rate-limit availability logging."""
        current_time = time.time()
        if current_time - self._last_availability_log > ERROR_LOG_RATE_LIMIT:
            self._last_availability_log = current_time
            return True
        return False

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
        new_interval = timedelta(seconds=seconds)
        if self.update_interval != new_interval:
            self.update_interval = new_interval

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch current device data."""
        start_time = time.time()
        if self._next_poll_attempt > start_time:
            if self.data is None:
                return {}
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
            if self.data is None:
                return {}
            raise UpdateFailed(f"Connection issue with {self.host}: {type(err).__name__}") from err
