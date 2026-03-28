"""Network handling for Eveus integration with silent offline mode."""
import logging
import asyncio
import time
import json
import random
from typing import Any, Optional, Set, Dict, List, Callable
from collections import deque

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CHARGING_UPDATE_INTERVAL,
    IDLE_UPDATE_INTERVAL,
    RETRY_DELAY,
    UPDATE_TIMEOUT,
    ERROR_LOG_RATE_LIMIT,
)
from .utils import get_safe_value
from .common_command import CommandManager

_LOGGER = logging.getLogger(__name__)


class EveusUpdater:
    """Updater for Eveus charger with silent offline handling."""

    def __init__(
        self, host: str, username: str, password: str, hass: HomeAssistant
    ) -> None:
        """Initialize updater."""
        self.host = host
        self.username = username
        self.password = password
        self._hass = hass

        # Data management
        self._data: Dict[str, Any] = {}
        self._available = True

        # Entity management
        self._entities: Set[Any] = set()
        self._update_callbacks: List[Callable] = []

        # Task management
        self._update_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()
        self._command_manager = CommandManager(self)

        # Simple connection metrics for diagnostic sensor
        self._success_count = 0
        self._total_count = 0
        self._consecutive_failures = 0
        self._last_success_time = time.time()
        self._latency_samples: deque = deque(maxlen=10)

        # Logging control
        self._last_availability_log = 0
        self._silent_mode = False
        self._offline_announced = False

    # -- Properties -----------------------------------------------------------

    @property
    def data(self) -> Dict[str, Any]:
        """Return current device data."""
        return self._data

    @property
    def available(self) -> bool:
        """Return availability status."""
        return self._available

    @property
    def hass(self) -> HomeAssistant:
        """Return Home Assistant instance."""
        return self._hass

    @property
    def connection_quality(self) -> Dict[str, Any]:
        """Connection metrics exposed for the diagnostic sensor."""
        success_rate = (self._success_count / max(self._total_count, 1)) * 100
        avg_latency = (
            (sum(self._latency_samples) / len(self._latency_samples))
            if self._latency_samples
            else 0.0
        )
        return {
            "success_rate": success_rate,
            "latency_avg": avg_latency,
            "consecutive_failures": self._consecutive_failures,
            "is_healthy": (
                success_rate > 80
                and time.time() - self._last_success_time < 300
            ),
        }

    @property
    def _is_likely_offline(self) -> bool:
        """Check if device appears to be powered off."""
        return (
            self._consecutive_failures > 10
            and time.time() - self._last_success_time > 600
        )

    # -- Session & entity management ------------------------------------------

    def get_session(self) -> aiohttp.ClientSession:
        """Get Home Assistant shared HTTP session."""
        return async_get_clientsession(self._hass)

    def register_entity(self, entity) -> None:
        """Register an entity for update notifications."""
        self._entities.add(entity)

    def register_update_callback(self, callback: Callable) -> None:
        """Register an update callback."""
        if callback not in self._update_callbacks:
            self._update_callbacks.append(callback)

    def unregister_update_callback(self, callback: Callable) -> None:
        """Unregister an update callback."""
        if callback in self._update_callbacks:
            self._update_callbacks.remove(callback)

    # -- Commands -------------------------------------------------------------

    async def send_command(self, command: str, value: Any) -> bool:
        """Send command to the device."""
        return await self._command_manager.send_command(command, value)

    # -- Internal helpers -----------------------------------------------------

    def _should_log(self) -> bool:
        """Rate-limit logging."""
        current_time = time.time()
        if current_time - self._last_availability_log > ERROR_LOG_RATE_LIMIT:
            self._last_availability_log = current_time
            return True
        return False

    def _record_success(self, response_time: float) -> None:
        """Record a successful poll."""
        self._success_count += 1
        self._total_count += 1
        self._consecutive_failures = 0
        self._last_success_time = time.time()
        self._latency_samples.append(response_time)
        self._silent_mode = False
        self._offline_announced = False

    def _record_failure(self) -> None:
        """Record a failed poll."""
        self._total_count += 1
        self._consecutive_failures += 1
        if self._consecutive_failures > 20:
            self._silent_mode = True

    def _notify_entities(self) -> None:
        """Notify all registered entities of a data update."""
        for entity in self._entities:
            if hasattr(entity, "hass") and entity.hass:
                try:
                    entity.async_write_ha_state()
                except Exception:
                    pass
        for cb in self._update_callbacks:
            try:
                cb()
            except Exception:
                pass

    # -- Polling --------------------------------------------------------------

    async def _update(self) -> None:
        """Poll the device for fresh data."""
        start_time = time.time()

        try:
            session = self.get_session()

            async with session.post(
                f"http://{self.host}/main",
                auth=aiohttp.BasicAuth(self.username, self.password),
                timeout=aiohttp.ClientTimeout(total=UPDATE_TIMEOUT),
            ) as response:
                response.raise_for_status()
                text = await response.text()

                new_data = json.loads(text)
                if not isinstance(new_data, dict):
                    raise ValueError(f"Expected dict, got {type(new_data)}")

                self._data = new_data
                self._record_success(time.time() - start_time)

                if not self._available:
                    self._available = True
                    if not self._silent_mode:
                        _LOGGER.info("Connection restored to %s", self.host)

                self._notify_entities()

        except (json.JSONDecodeError, ValueError) as err:
            if not self._silent_mode:
                _LOGGER.debug("Parse error from %s: %s", self.host, err)
            self._record_failure()
        except (
            aiohttp.ClientResponseError,
            aiohttp.ClientConnectorError,
            asyncio.TimeoutError,
        ) as err:
            self._handle_error(err)
        except Exception as err:
            self._handle_error(err)

    def _handle_error(self, error: Exception) -> None:
        """Handle a poll error and update availability."""
        self._record_failure()
        was_available = self._available
        self._available = False

        if was_available:
            # First failure after being online — clear data and notify entities
            self._data = {}

            if not self._silent_mode and self._should_log():
                _LOGGER.debug(
                    "Connection issue with %s: %s", self.host, type(error).__name__
                )

            self._notify_entities()

        elif self._is_likely_offline and not self._offline_announced:
            # Prolonged failure — announce offline once, then go silent
            _LOGGER.info(
                "Device %s appears offline, reducing poll frequency", self.host
            )
            self._offline_announced = True

    # -- Lifecycle ------------------------------------------------------------

    async def async_start_updates(self) -> None:
        """Start the polling loop."""
        if self._update_task is None:
            self._shutdown_event.clear()
            self._update_task = asyncio.create_task(self._update_loop())
            _LOGGER.debug("Started update loop for %s", self.host)

    async def _update_loop(self) -> None:
        """Polling loop with adaptive interval and exponential backoff."""
        consecutive_failures = 0

        while not self._shutdown_event.is_set():
            try:
                await self._update()
                consecutive_failures = 0

                is_charging = get_safe_value(self._data, "state", int) == 4
                is_active = (
                    get_safe_value(self._data, "powerMeas", float, 0) > 100
                )
                interval = (
                    CHARGING_UPDATE_INTERVAL
                    if (is_charging or is_active)
                    else IDLE_UPDATE_INTERVAL
                )
                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as err:
                consecutive_failures += 1

                if (
                    not self._silent_mode
                    and consecutive_failures <= 3
                    and self._should_log()
                ):
                    _LOGGER.debug("Update error for %s: %s", self.host, err)

                if self._is_likely_offline:
                    delay = min(RETRY_DELAY * 4, 300)
                else:
                    delay = min(
                        RETRY_DELAY * (2 ** (consecutive_failures - 1)), 60
                    )
                delay += random.uniform(0, delay * 0.1)
                await asyncio.sleep(delay)

    async def async_shutdown(self) -> None:
        """Shut down the updater and release resources."""
        _LOGGER.debug("Shutting down updater for %s", self.host)

        self._shutdown_event.set()

        if self._update_task:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass
            self._update_task = None

        _LOGGER.debug("Updater shutdown complete for %s", self.host)
