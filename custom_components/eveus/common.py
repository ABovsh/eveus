"""Common code for Eveus integration."""
from __future__ import annotations

import logging
import asyncio
import time
import json
from datetime import datetime
from typing import Any, Optional
from contextlib import asynccontextmanager

import aiohttp
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers import aiohttp_client
from homeassistant.components.sensor import SensorEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import utcnow

from .const import DOMAIN, SCAN_INTERVAL
from .utils import get_device_info

_LOGGER = logging.getLogger(__name__)

# Constants
RETRY_DELAY = 15
COMMAND_TIMEOUT = 25
UPDATE_TIMEOUT = 20
MAX_RETRIES = 3
ERROR_COOLDOWN = 300  # 5 minutes
SESSION_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=5)

class EveusError(HomeAssistantError):
    """Base class for Eveus errors."""

class EveusConnectionError(EveusError):
    """Error indicating connection issues."""

class EveusResponseError(EveusError):
    """Error indicating invalid response."""

class CommandManager:
    """Manage command execution and retries."""
    
    def __init__(self, updater: "EveusUpdater"):
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
        """Execute command with retries and rate limiting."""
        async with self._lock:
            retries = 3
            delay = 1

            for attempt in range(retries):
                try:
                    # Rate limiting
                    time_since_last = time.time() - self._last_command_time
                    if time_since_last < 1:
                        await asyncio.sleep(1 - time_since_last)

                    session = await self._updater._get_session()
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

                except aiohttp.ClientError as err:
                    if attempt == retries - 1:
                        _LOGGER.error(
                            "Command %s failed after %d retries: %s",
                            command, retries, str(err)
                        )
                        raise
                    _LOGGER.warning(
                        "Command %s failed (attempt %d/%d): %s",
                        command, attempt + 1, retries, str(err)
                    )
                    await asyncio.sleep(delay)
                    delay *= 2
                except Exception as err:
                    _LOGGER.error(
                        "Unexpected error executing command %s: %s",
                        command, str(err)
                    )
                    raise

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

class EveusUpdater:
    """Class to handle Eveus data updates."""

    def __init__(self, host: str, username: str, password: str, hass: HomeAssistant) -> None:
        """Initialize the updater."""
        self.host = host
        self.username = username
        self.password = password
        self._hass = hass
        self._data: dict = {}
        self._available = True
        self._session: Optional[aiohttp.ClientSession] = None
        self._entities = set()
        self._update_task: Optional[asyncio.Task] = None
        self._last_update = 0
        self._update_lock = asyncio.Lock()
        self._retry_count = 0
        self._failed_requests = 0
        self._consecutive_errors = 0
        self._last_error_time = 0
        self._last_error_type = None
        self._shutdown_event = asyncio.Event()
        self._command_manager = CommandManager(self)

    @property
    def data(self) -> dict:
        """Return the latest data."""
        return self._data.copy()

    @property
    def available(self) -> bool:
        """Return if updater is available."""
        return self._available

    @property
    def failed_requests(self) -> int:
        """Return number of failed requests."""
        return self._failed_requests

    @property
    def last_error_time(self) -> float:
        """Return timestamp of last error."""
        return self._last_error_time

    @property
    def last_error_type(self) -> str:
        """Return type of last error."""
        return self._last_error_type

    @property
    def consecutive_errors(self) -> int:
        """Return number of consecutive errors."""
        return self._consecutive_errors

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create client session."""
        if not self._session or self._session.closed:
            self._session = aiohttp_client.async_get_clientsession(self._hass)
        return self._session

    def register_entity(self, entity: BaseEveusEntity) -> None:
        """Register an entity for updates."""
        if entity not in self._entities:
            self._entities.add(entity)
            _LOGGER.debug("Registered entity: %s", entity.name)

    async def send_command(self, command: str, value: Any) -> bool:
        """Send command to device."""
        return await self._command_manager.send_command(command, value)

    async def _update(self) -> None:
        """Update the data."""
        try:
            session = await self._get_session()
            async with session.post(
                f"http://{self.host}/main",
                auth=aiohttp.BasicAuth(self.username, self.password),
                timeout=aiohttp.ClientTimeout(total=UPDATE_TIMEOUT),
            ) as response:
                response.raise_for_status()
                text = await response.text()
                
                try:
                    data = json.loads(text)
                    if not isinstance(data, dict):
                        raise ValueError(f"Unexpected data type: {type(data)}")
                    
                    if data != self._data:
                        self._data = data
                        self._available = True
                        self._last_update = time.time()
                        self._retry_count = 0
                        self._failed_requests = 0
                        self._consecutive_errors = 0

                        # Update all registered entities
                        for entity in self._entities:
                            if hasattr(entity, 'hass') and entity.hass:
                                try:
                                    entity.async_write_ha_state()
                                except Exception as err:
                                    _LOGGER.error(
                                        "Error updating entity %s: %s",
                                        getattr(entity, 'name', 'unknown'),
                                        str(err)
                                    )
                    
                except ValueError as err:
                    _LOGGER.error("Invalid JSON received: %s", err)
                    self._handle_error("Data error", err)
                    raise

        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            self._handle_error("Connection error", err)
            
        except Exception as err:
            self._handle_error("Unexpected error", err)

    def _handle_error(self, error_type: str, error: Exception) -> None:
        """Handle update errors."""
        current_time = time.time()
        self._failed_requests += 1
        self._consecutive_errors += 1
        self._last_error_time = current_time
        self._last_error_type = error_type
        
        # Reset consecutive errors if enough time has passed
        if (current_time - self._last_error_time) > ERROR_COOLDOWN:
            self._consecutive_errors = 1
        
        if self._retry_count < MAX_RETRIES:
            self._retry_count += 1
            _LOGGER.warning(
                "%s for %s: %s (attempt %d/%d)",
                error_type,
                self.host,
                str(error),
                self._retry_count,
                MAX_RETRIES
            )
        else:
            self._available = False
            _LOGGER.error(
                "%s for %s: %s (max retries reached)",
                error_type,
                self.host,
                str(error)
            )

    async def async_start_updates(self) -> None:
        """Start the update loop."""
        if self._update_task is None:
            self._shutdown_event.clear()
            self._update_task = asyncio.create_task(self.update_loop())
            await self._command_manager.start()
            _LOGGER.debug("Started update loop for %s", self.host)

    async def update_loop(self) -> None:
        """Handle update loop with dynamic intervals."""
        _LOGGER.debug("Starting update loop for %s", self.host)
    
        while not self._shutdown_event.is_set():
            try:
                async with self._update_lock:
                    await self._update()
                    
                if self._retry_count > 0:
                    await asyncio.sleep(RETRY_DELAY)
                else:
                    # Check if charging (state 4 is "Charging")
                    is_charging = self._data.get("state") == 4
                    # 30 seconds if charging, 60 seconds if not
                    sleep_time = 30 if is_charging else 60
                    await asyncio.sleep(sleep_time)
                        
            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.error("Error in update loop: %s", str(err))
                await asyncio.sleep(RETRY_DELAY)

    async def async_shutdown(self) -> None:
        """Shutdown the updater."""
        self._shutdown_event.set()
        
        await self._command_manager.stop()

        if self._update_task:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass
            self._update_task = None

class BaseEveusEntity(RestoreEntity, Entity):
    """Base implementation for Eveus entities."""

    ENTITY_NAME: str = None
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the entity."""
        super().__init__()
        self._updater = updater
        self._updater.register_entity(self)

        if self.ENTITY_NAME is None:
            raise NotImplementedError("ENTITY_NAME must be defined in child class")

        self._attr_name = self.ENTITY_NAME
        self._attr_unique_id = f"eveus_{self.ENTITY_NAME.lower().replace(' ', '_')}"

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self._updater.available

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device information."""
        return get_device_info(self._updater.host, self._updater.data)

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state:
            await self._async_restore_state(state)
        await self._updater.async_start_updates()

    async def _async_restore_state(self, state) -> None:
        """Restore previous state."""
        pass

class EveusSensorBase(BaseEveusEntity, SensorEntity):
    """Base sensor entity for Eveus."""
    
    def __init__(self, updater: EveusUpdater) -> None:
        """Initialize the sensor."""
        super().__init__(updater)
        self._attr_native_value = None

    @property
    def native_value(self) -> Any | None:
        """Return sensor value."""
        return self._attr_native_value

async def send_eveus_command(
    session: aiohttp.ClientSession,
    host: str,
    username: str,
    password: str,
    command: str,
    value: Any
) -> bool:
    """Send command to Eveus device."""
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
