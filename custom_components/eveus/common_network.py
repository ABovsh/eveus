"""Network handling and state management for Eveus integration."""
import logging
import asyncio
import time
import json
from typing import Any, Optional
from collections import deque, Counter

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client

from .const import (
    CHARGING_UPDATE_INTERVAL,
    IDLE_UPDATE_INTERVAL,
    RETRY_DELAY,
    UPDATE_TIMEOUT,
    ERROR_COOLDOWN
)
from .utils import get_safe_value
from .common_command import CommandManager

_LOGGER = logging.getLogger(__name__)

class NetworkManager:
    """Network resilience management."""
    def __init__(self, updater: "EveusUpdater"):
        """Initialize network manager."""
        self._updater = updater
        self._offline_commands = []
        self._last_successful_state = None
        self._reconnect_attempts = 0
        self._quality_metrics = {
            'latency': deque(maxlen=100),
            'success_rate': deque(maxlen=100),
            'error_types': Counter(),
            'last_errors': deque(maxlen=10)
        }
        self._request_timestamps = deque(maxlen=100)

    @property
    def connection_quality(self) -> dict:
        """Get connection quality metrics."""
        if not self._quality_metrics['latency']:
            return {
                'latency_avg': 0,
                'success_rate': 100,
                'recent_errors': 0,
                'requests_per_minute': 0
            }

        now = time.time()
        recent_requests = sum(1 for t in self._request_timestamps 
                            if now - t < 60)

        return {
            'latency_avg': sum(self._quality_metrics['latency']) / len(self._quality_metrics['latency']),
            'success_rate': (sum(self._quality_metrics['success_rate']) / len(self._quality_metrics['success_rate'])) * 100,
            'recent_errors': len(self._quality_metrics['last_errors']),
            'requests_per_minute': recent_requests
        }

    def update_metrics(self, response_time: float, success: bool, error_type: str = None) -> None:
        """Update connection quality metrics."""
        self._quality_metrics['latency'].append(response_time)
        self._quality_metrics['success_rate'].append(1 if success else 0)
        self._request_timestamps.append(time.time())
        
        if not success and error_type:
            self._quality_metrics['error_types'][error_type] += 1
            self._quality_metrics['last_errors'].append({
                'type': error_type,
                'timestamp': time.time()
            })

    def cache_state(self, state_data: dict) -> None:
        """Cache last known good state."""
        self._last_successful_state = {
            'timestamp': time.time(),
            'data': state_data.copy(),
            'charging_state': state_data.get('state'),
            'critical_values': {
                'current': state_data.get('currentSet'),
                'enabled': state_data.get('evseEnabled')
            }
        }

    def get_cached_state(self) -> dict | None:
        """Get cached state if valid."""
        if not self._last_successful_state:
            return None
            
        age = time.time() - self._last_successful_state['timestamp']
        if age > ERROR_COOLDOWN:
            return None
            
        return self._last_successful_state['data']

class EveusUpdater:
    """Main updater class with enhanced network handling."""

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
        self._shutdown_event = asyncio.Event()
        self._command_manager = CommandManager(self)
        self._network = NetworkManager(self)

    @property
    def data(self) -> dict:
        """Return the latest data."""
        return self._data.copy()

    @property
    def available(self) -> bool:
        """Return if updater is available."""
        return self._available

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create client session."""
        if not self._session or self._session.closed:
            self._session = aiohttp_client.async_get_clientsession(self._hass)
        return self._session

    def register_entity(self, entity) -> None:
        """Register an entity for updates."""
        if entity not in self._entities:
            self._entities.add(entity)
            _LOGGER.debug("Registered entity: %s", entity.name)

    async def send_command(self, command: str, value: Any) -> bool:
        """Send command to device."""
        return await self._command_manager.send_command(command, value)

    async def _update(self) -> None:
        """Update device data."""
        try:
            start_time = time.time()
            session = await self._get_session()
            
            async with session.post(
                f"http://{self.host}/main",
                auth=aiohttp.BasicAuth(self.username, self.password),
                timeout=UPDATE_TIMEOUT,
            ) as response:
                response.raise_for_status()
                text = await response.text()
                
                data = json.loads(text)
                if not isinstance(data, dict):
                    raise ValueError(f"Unexpected data type: {type(data)}")
                
                if data != self._data:
                    self._data = data
                    self._available = True
                    self._network.cache_state(data)
                    self._network.update_metrics(
                        response_time=time.time() - start_time,
                        success=True
                    )

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
                
        except Exception as err:
            self._network.update_metrics(
                response_time=time.time() - start_time,
                success=False,
                error_type=type(err).__name__
            )
            # Try to use cached state
            cached_state = self._network.get_cached_state()
            if cached_state:
                self._data = cached_state
            _LOGGER.error("Update failed: %s", str(err))

    async def async_start_updates(self) -> None:
        """Start the update loop."""
        if self._update_task is None:
            self._shutdown_event.clear()
            self._update_task = asyncio.create_task(self.update_loop())
            await self._command_manager.start()
            _LOGGER.debug("Started update loop for %s", self.host)

    async def update_loop(self) -> None:
        """Handle update loop with dynamic intervals."""
        while not self._shutdown_event.is_set():
            try:
                await self._update()
                # Use shorter interval if charging
                is_charging = get_safe_value(self._data, "state", int) == 4
                interval = CHARGING_UPDATE_INTERVAL if is_charging else IDLE_UPDATE_INTERVAL
                await asyncio.sleep(interval)
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
