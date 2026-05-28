"""Command handling for Eveus integration."""
import logging
import asyncio
import random
import time
from typing import Any
from urllib.parse import urlencode

import aiohttp
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import COMMAND_TIMEOUT, ERROR_LOG_RATE_LIMIT
from .utils import RateLog

_COMMAND_TIMEOUT_OBJ: aiohttp.ClientTimeout = aiohttp.ClientTimeout(total=COMMAND_TIMEOUT)

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
        self._error_log = RateLog()

    @property
    def consecutive_failures(self) -> int:
        """Return consecutive command failures."""
        return self._consecutive_failures

    def _should_log_error(self) -> bool:
        """Rate limit error logging."""
        return self._error_log.should_log(ERROR_LOG_RATE_LIMIT)

    async def _sleep_backoff(self, attempt: int) -> None:
        """Wait the per-attempt backoff window (with jitter) before retrying."""
        delay = _COMMAND_RETRY_BACKOFF[attempt] + random.uniform(0, _COMMAND_RETRY_JITTER)
        await asyncio.sleep(delay)

    async def send_command(self, command: str, value: Any, *, retry: bool = True) -> bool:
        """Send command with rate limiting, retry/backoff, and error handling."""
        async with self._lock:
            # Rate limit: minimum 1 second between commands
            time_since_last = time.time() - self._last_command_time
            if time_since_last < 1:
                await asyncio.sleep(1 - time_since_last)

            try:
                last_error: Exception | None = None
                retry_attempts = _COMMAND_RETRY_ATTEMPTS if retry else 0
                for attempt in range(retry_attempts + 1):
                    try:
                        return await self._post_command(command, value)
                    except aiohttp.ClientResponseError as err:
                        if err.status == 401:
                            self._consecutive_failures += 1
                            raise ConfigEntryAuthFailed(
                                "Eveus charger rejected credentials"
                            ) from err
                        # Permanent client/server-routing errors won't fix
                        # themselves: don't burn the retry budget on them.
                        if err.status not in (408, 425, 429, 500, 502, 503, 504):
                            last_error = err
                            break
                        last_error = err
                        if attempt >= retry_attempts:
                            break
                        await self._sleep_backoff(attempt)
                    except (
                        aiohttp.ClientConnectorError,
                        aiohttp.ClientError,
                        asyncio.TimeoutError,
                    ) as err:
                        last_error = err
                        if attempt >= retry_attempts:
                            break
                        await self._sleep_backoff(attempt)

                self._consecutive_failures += 1
                if self._consecutive_failures <= 5 and self._should_log_error():
                    # Log only the error type — ClientResponseError.__str__ embeds
                    # the request URL (the charger host), which we scrub elsewhere.
                    _LOGGER.debug(
                        "Command %s failed: %s", command, type(last_error).__name__
                    )
                return False

            except ConfigEntryAuthFailed:
                raise
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

        payload = urlencode({"pageevent": command, command: value})
        async with session.post(
            self._updater.url_for("/pageEvent"),
            auth=self._updater._basic_auth,
            headers={"Content-type": "application/x-www-form-urlencoded"},
            data=payload,
            timeout=_COMMAND_TIMEOUT_OBJ,
        ) as response:
            response.raise_for_status()
            self._consecutive_failures = 0
            return True
