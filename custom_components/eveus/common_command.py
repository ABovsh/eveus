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
    
    def __init__(self, updater):
        """Initialize command manager."""
        self._updater = updater
        self._queue = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._last_command_time = 0
        self._lock = asyncio.Lock()

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
                try:
                    if not future.done():
                        result = await asyncio.wait_for(
                            self._execute_command(command, value),
                            timeout=COMMAND_TIMEOUT
                        )
                        future.set_result(result)
                except asyncio.TimeoutError:
                    if not future.done():
                        future.set_exception(asyncio.TimeoutError("Command timed out"))
                except Exception as err:
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
        """Execute command with retries."""
        async with self._lock:
            try:
                time_since_last = time.time() - self._last_command_time
                if time_since_last < 1:
                    await asyncio.sleep(1 - time_since_last)

                session = await self._updater._get_session()
                start_time = time.time()
                
                async with session.post(
                    f"http://{self._updater.host}/pageEvent",
                    auth=aiohttp.BasicAuth(
                        self._updater.username, 
                        self._updater.password
                    ),
                    headers={"Content-type": "application/x-www-form-urlencoded"},
                    data=f"pageevent={command}&{command}={value}",
                    timeout=COMMAND_TIMEOUT,
                ) as response:
                    response.raise_for_status()
                    self._last_command_time = time.time()
                    return True

            except Exception as err:
                _LOGGER.error("Command execution failed: %s", err)
                return False

    async def send_command(self, command: str, value: Any) -> bool:
        """Send command through queue."""
        try:
            future = asyncio.get_running_loop().create_future()
            await self._queue.put((command, value, future))
            return await asyncio.wait_for(future, timeout=30)
        except asyncio.TimeoutError:
            _LOGGER.error("Command execution timed out")
            return False
        except Exception as err:
            _LOGGER.error("Command execution failed: %s", err)
            return False

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
        async with session.post(
            f"http://{host}/pageEvent",
            auth=aiohttp.BasicAuth(username, password),
            headers={"Content-type": "application/x-www-form-urlencoded"},
            data=f"pageevent={command}&{command}={value}",
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            response.raise_for_status()
            return True
    except Exception as err:
        _LOGGER.error("Command %s failed: %s", command, str(err))
        return False
