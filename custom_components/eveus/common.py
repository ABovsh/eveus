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

_LOGGER = logging.getLogger(__name__)

# Constants
RETRY_DELAY = 15
COMMAND_TIMEOUT = 5
UPDATE_TIMEOUT = 20
MAX_RETRIES = 3
ERROR_COOLDOWN = 300  # 5 minutes
SESSION_TIMEOUT = aiohttp.ClientTimeout(total=10, connect=3)

class EveusError(HomeAssistantError):
    """Base class for Eveus errors."""

class EveusConnectionError(EveusError):
    """Error indicating connection issues."""

class EveusResponseError(EveusError):
    """Error indicating invalid response."""

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
        self._command_lock = asyncio.Lock()
        self._retry_count = 0
        self._failed_requests = 0
        self._consecutive_errors = 0
        self._last_error_time = 0
        self._last_error_type = None
        self._shutdown_event = asyncio.Event()
        self._last_command_time = 0
        self._command_queue = asyncio.Queue()
        self._command_task: Optional[asyncio.Task] = None

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

    async def _process_command_queue(self) -> None:
        """Process commands in the queue."""
        while not self._shutdown_event.is_set():
            try:
                command, value, future = await self._command_queue.get()
                try:
                    result = await self._send_command_internal(command, value)
                    future.set_result(result)
                except Exception as err:
                    future.set_exception(err)
                finally:
                    self._command_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.error("Error processing command queue: %s", err)
                await asyncio.sleep(1)

    async def _send_command_internal(self, command: str, value: Any) -> bool:
        """Internal command sending with rate limiting."""
        async with self._command_lock:
            # Rate limiting
            time_since_last = time.time() - self._last_command_time
            if time_since_last < 1:  # Minimum 1 second between commands
                await asyncio.sleep(1 - time_since_last)

            try:
                session = await self._get_session()
                async with session.post(
                    f"http://{self.host}/pageEvent",
                    auth=aiohttp.BasicAuth(self.username, self.password),
                    headers={"Content-type": "application/x-www-form-urlencoded"},
                    data=f"pageevent={command}&{command}={value}",
                    timeout=COMMAND_TIMEOUT,
                ) as response:
                    response.raise_for_status()
                    self._last_command_time = time.time()
                    return True
            except Exception as err:
                _LOGGER.error("Command %s failed: %s", command, str(err))
                raise

    async def send_command(self, command: str, value: Any) -> bool:
        """Send command to device."""
        future = self._hass.loop.create_future()
        await self._command_queue.put((command, value, future))
        try:
            return await future
        except Exception as err:
            _LOGGER.error("Command execution failed: %s", err)
            return False

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
                        update_tasks = []
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
            self._command_task = asyncio.create_task(self._process_command_queue())
            _LOGGER.debug("Started update loop for %s", self.host)

    async def update_loop(self) -> None:
        """Handle update loop."""
        _LOGGER.debug("Starting update loop for %s", self.host)
        while not self._shutdown_event.is_set():
            try:
                async with self._update_lock:
                    await self._update()
                    
                if self._retry_count > 0:
                    await asyncio.sleep(RETRY_DELAY)
                else:
                    await asyncio.sleep(SCAN_INTERVAL.total_seconds())
                    
            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.error("Error in update loop: %s", str(err))
                await asyncio.sleep(RETRY_DELAY)

    async def async_shutdown(self) -> None:
        """Shutdown the updater."""
        self._shutdown_event.set()
        
        if self._command_task:
            self._command_task.cancel()
            try:
                await self._command_task
            except asyncio.CancelledError:
                pass
            self._command_task = None

        if self._update_task:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass
            self._update_task = None

        # Clear queue
        while not self._command_queue.empty():
            try:
                _, _, future = self._command_queue.get_nowait()
                if not future.done():
                    future.set_exception(EveusError("Updater shutting down"))
            except asyncio.QueueEmpty:
                break

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
        return {
            "identifiers": {(DOMAIN, self._updater.host)},
            "name": "Eveus EV Charger",
            "manufacturer": "Eveus",
            "model": "Eveus EV Charger",
            "sw_version": self._updater.data.get('verFWMain', 'Unknown'),
            "configuration_url": f"http://{self._updater.host}",
        }

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

    async def async_will_remove_from_hass(self) -> None:
        """Handle entity removal."""
        # Note: Don't call updater.async_shutdown() here as other entities might still be using it
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
            timeout=aiohttp.ClientTimeout(total=5)
        ) as response:
            response.raise_for_status()
            return True
    except Exception as err:
        _LOGGER.error("Command %s failed: %s", command, str(err))
        return False
