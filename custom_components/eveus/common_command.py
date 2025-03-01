"""Command handling for Eveus integration."""
import logging
import asyncio
import time
from typing import Any, Optional

import aiohttp

from .const import COMMAND_TIMEOUT

_LOGGER = logging.getLogger(__name__)

class CommandManager:
    """Manage command execution and retries."""
    
    def __init__(self, updater) -> None:
        """Initialize command manager."""
        self._updater = updater
        self._queue = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._last_command_time = 0
        self._lock = asyncio.Lock()
        self._timeout = COMMAND_TIMEOUT

    async def start(self) -> None:
        """Start command processing."""
        if not self._task:
            self._task = asyncio.create_task(self._process_queue())

    async def stop(self) -> None:
        """Stop command processing."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _process_queue(self) -> None:
        """Process commands in queue."""
        while True:
            try:
                command, value, future = await self._queue.get()
                
                # Execute command with timeout protection
                try:
                    if not future.done():
                        # Use a more reliable timeout approach
                        try:
                            result = await asyncio.wait_for(
                                self._execute_command(command, value),
                                timeout=self._timeout
                            )
                            if not future.done():
                                future.set_result(result)
                        except asyncio.TimeoutError:
                            _LOGGER.warning("Command %s timed out after %s seconds", command, self._timeout)
                            if not future.done():
                                future.set_result(False)  # Set result to False instead of exception
                        except Exception as err:
                            _LOGGER.error("Command execution error: %s", err)
                            if not future.done():
                                future.set_exception(err)
                except Exception as err:
                    _LOGGER.error("Error in command processing: %s", err)
                    if not future.done():
                        future.set_exception(err)
                finally:
                    self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.error("Error processing command queue: %s", err)
                await asyncio.sleep(1)

    async def _execute_command(self, command: str, value: Any) -> bool:
        """Execute command with rate limiting and better error handling."""
        async with self._lock:
            # Rate limit commands
            time_since_last = time.time() - self._last_command_time
            if time_since_last < 1:
                await asyncio.sleep(1 - time_since_last)
            
            try:
                session = await self._updater._get_session()
                
                # Use a shorter timeout for the actual HTTP request
                timeout = aiohttp.ClientTimeout(total=min(15, self._timeout - 2))
                
                async with session.post(
                    f"http://{self._updater.host}/pageEvent",
                    auth=aiohttp.BasicAuth(
                        self._updater.username, 
                        self._updater.password
                    ),
                    headers={"Content-type": "application/x-www-form-urlencoded"},
                    data=f"pageevent={command}&{command}={value}",
                    timeout=timeout,
                ) as response:
                    response.raise_for_status()
                    self._last_command_time = time.time()
                    return True

            except aiohttp.ClientResponseError as err:
                _LOGGER.error("Command %s HTTP error %d: %s", command, err.status, err)
                return False
            except aiohttp.ClientConnectorError as err:
                _LOGGER.error("Connection error for command %s: %s", command, err)
                return False
            except asyncio.TimeoutError:
                _LOGGER.error("Command %s timed out", command)
                return False
            except Exception as err:
                _LOGGER.error("Command %s failed with unexpected error: %s", command, err)
                return False

    async def send_command(self, command: str, value: Any) -> bool:
        """Send command through queue with timeout protection."""
        try:
            future = asyncio.get_running_loop().create_future()
            await self._queue.put((command, value, future))
            
            # Use a significantly increased timeout for waiting on the future
            # This helps prevent timeouts in the main thread
            return await asyncio.wait_for(future, timeout=60)
        except asyncio.TimeoutError:
            _LOGGER.error("Command %s queue processing timed out", command)
            return False
        except Exception as err:
            _LOGGER.error("Command %s failed in queue: %s", command, err)
            return False


# Simple version for direct use without queue
async def send_eveus_command(
    session: aiohttp.ClientSession,
    host: str,
    username: str,
    password: str,
    command: str,
    value: Any
) -> bool:
    """Legacy command function maintained for compatibility."""
    try:
        timeout = aiohttp.ClientTimeout(total=COMMAND_TIMEOUT)
        async with session.post(
            f"http://{host}/pageEvent",
            auth=aiohttp.BasicAuth(username, password),
            headers={"Content-type": "application/x-www-form-urlencoded"},
            data=f"pageevent={command}&{command}={value}",
            timeout=timeout
        ) as response:
            response.raise_for_status()
            return True
    except Exception as err:
        _LOGGER.error("Command %s failed: %s", command, str(err))
        return False
