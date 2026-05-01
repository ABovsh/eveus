"""Command handling for Eveus integration."""
import logging
import asyncio
import random
import time
from typing import Any

import aiohttp

from .const import COMMAND_TIMEOUT, ERROR_LOG_RATE_LIMIT

_LOGGER = logging.getLogger(__name__)

# Retry transient command failures a couple of times before giving up.
# Most failed-toggle reports are single-packet WiFi loss, not real device
# rejections, so a tiny backoff window dramatically improves perceived
# reliability without making real failures slow to surface.
_COMMAND_RETRY_ATTEMPTS = 2
_COMMAND_RETRY_BACKOFF: tuple[float, ...] = (0.5, 1.5)
_COMMAND_RETRY_JITTER = 0.25


class CommandManager:
    """Manage command execution with rate limiting and error handling."""

    def __init__(self, updater) -> None:
        """Initialize command manager."""
        self._updater = updater
        self._lock = asyncio.Lock()
        self._last_command_time = 0
        self._consecutive_failures = 0
        self._last_error_log = 0

    def _should_log_error(self) -> bool:
        """Rate limit error logging."""
        current_time = time.time()
        if current_time - self._last_error_log > ERROR_LOG_RATE_LIMIT:
            self._last_error_log = current_time
            return True
        return False

    async def send_command(self, command: str, value: Any) -> bool:
        """Send command with rate limiting, retry/backoff, and error handling."""
        async with self._lock:
            # Rate limit: minimum 1 second between commands
            time_since_last = time.time() - self._last_command_time
            if time_since_last < 1:
                await asyncio.sleep(1 - time_since_last)

            try:
                last_error: Exception | None = None
                for attempt in range(_COMMAND_RETRY_ATTEMPTS + 1):
                    try:
                        return await self._post_command(command, value)
                    except (
                        aiohttp.ClientResponseError,
                        aiohttp.ClientConnectorError,
                        aiohttp.ClientError,
                        asyncio.TimeoutError,
                    ) as err:
                        last_error = err
                        if attempt >= _COMMAND_RETRY_ATTEMPTS:
                            break
                        delay = _COMMAND_RETRY_BACKOFF[attempt] + random.uniform(
                            0, _COMMAND_RETRY_JITTER
                        )
                        await asyncio.sleep(delay)

                self._consecutive_failures += 1
                if self._consecutive_failures <= 5 and self._should_log_error():
                    _LOGGER.debug("Command %s failed: %s", command, last_error)
                return False

            except Exception as err:
                self._consecutive_failures += 1
                if self._should_log_error():
                    _LOGGER.debug(
                        "Command %s unexpected error: %s",
                        command,
                        err,
                        exc_info=True,
                    )
                return False
            finally:
                self._last_command_time = time.time()

    async def _post_command(self, command: str, value: Any) -> bool:
        """Issue a single HTTP request to the charger and return success."""
        session = self._updater.get_session()
        timeout = aiohttp.ClientTimeout(total=COMMAND_TIMEOUT)

        async with session.post(
            f"http://{self._updater.host}/pageEvent",
            auth=aiohttp.BasicAuth(
                self._updater.username,
                self._updater.password,
            ),
            headers={"Content-type": "application/x-www-form-urlencoded"},
            data=f"pageevent={command}&{command}={value}",
            timeout=timeout,
        ) as response:
            response.raise_for_status()
            self._consecutive_failures = 0
            return True
