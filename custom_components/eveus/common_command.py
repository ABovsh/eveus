"""Command handling for Eveus integration."""
import logging
import asyncio
import time
from typing import Any

import aiohttp

from .const import COMMAND_TIMEOUT, ERROR_LOG_RATE_LIMIT

_LOGGER = logging.getLogger(__name__)


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
        """Send command with rate limiting and error handling."""
        async with self._lock:
            # Rate limit: minimum 1 second between commands
            time_since_last = time.time() - self._last_command_time
            if time_since_last < 1:
                await asyncio.sleep(1 - time_since_last)

            try:
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
                    self._last_command_time = time.time()
                    self._consecutive_failures = 0
                    return True

            except (aiohttp.ClientResponseError, aiohttp.ClientConnectorError,
                    asyncio.TimeoutError) as err:
                self._consecutive_failures += 1
                if self._consecutive_failures <= 5 and self._should_log_error():
                    _LOGGER.debug("Command %s failed: %s", command, err)
                return False
            except Exception as err:
                self._consecutive_failures += 1
                if self._should_log_error():
                    _LOGGER.debug("Command %s unexpected error: %s", command, err)
                return False


async def send_eveus_command(
    session: aiohttp.ClientSession,
    host: str,
    username: str,
    password: str,
    command: str,
    value: Any,
) -> bool:
    """Standalone command function for backward compatibility."""

    class _LegacyUpdater:
        """Small adapter used by the legacy command helper."""

        def __init__(self) -> None:
            self.host = host
            self.username = username
            self.password = password

        def get_session(self) -> aiohttp.ClientSession:
            return session

    return await CommandManager(_LegacyUpdater()).send_command(command, value)
