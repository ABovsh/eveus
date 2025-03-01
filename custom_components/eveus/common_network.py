"""Network handling and state management for Eveus integration."""
import logging
import asyncio
import time
import json
from typing import Any, Optional, Set, Dict
from collections import deque, Counter

import aiohttp
from homeassistant.core import HomeAssistant, callback
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

# Updated part of common_network.py to track connection quality history

class NetworkManager:
    """Network resilience management."""
    def __init__(self, host: str) -> None:
        """Initialize network manager."""
        self.host = host
        self._last_successful_state = None
        self._reconnect_attempts = 0
        self._quality_metrics = {
            'latency': deque(maxlen=20),
            'success_rate': deque(maxlen=20),
            'success_rate_history': deque(maxlen=100),  # Added success rate history
            'error_types': Counter(),
            'last_errors': deque(maxlen=10),
            'last_successful_connection': time.time(),  # Added timestamp tracking
        }
        self._request_timestamps = deque(maxlen=30)

    @property
    def connection_quality(self) -> dict:
        """Get connection quality metrics."""
        if not self._quality_metrics['latency']:
            return {
                'latency_avg': 0,
                'success_rate': 100,
                'recent_errors': 0,
                'requests_per_minute': 0,
                'success_rate_history': list(self._quality_metrics['success_rate_history']),
                'last_successful_connection': self._quality_metrics.get('last_successful_connection', time.time())
            }

        now = time.time()
        recent_requests = sum(1 for t in self._request_timestamps 
                            if now - t < 60)
        
        # Calculate success rate
        success_rate = (sum(self._quality_metrics['success_rate']) / 
                     max(len(self._quality_metrics['success_rate']), 1)) * 100
                     
        # Store in history
        self._quality_metrics['success_rate_history'].append(success_rate)

        return {
            'latency_avg': sum(self._quality_metrics['latency']) / max(len(self._quality_metrics['latency']), 1),
            'success_rate': success_rate,
            'recent_errors': len(self._quality_metrics['last_errors']),
            'requests_per_minute': recent_requests,
            'success_rate_history': list(self._quality_metrics['success_rate_history']),
            'last_successful_connection': self._quality_metrics.get('last_successful_connection', time.time())
        }

    def update_metrics(self, response_time: float, success: bool, error_type: str = None) -> None:
        """Update connection quality metrics."""
        self._quality_metrics['latency'].append(response_time)
        self._quality_metrics['success_rate'].append(1 if success else 0)
        self._request_timestamps.append(time.time())
        
        if success:
            self._quality_metrics['last_successful_connection'] = time.time()
            
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
            'data': state_data.copy()
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
        self._data: Dict[str, Any] = {}
        self._available = True
        self._session: Optional[aiohttp.ClientSession] = None
        self._entities: Set[Any] = set()
        self._update_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()
        self._command_manager = CommandManager(self)
        self._network = NetworkManager(host)
        self._entity_update_callbacks = []

    @property
    def data(self) -> dict:
        """Return the latest data."""
        return self._data.copy()

    @property
    def available(self) -> bool:
        """Return if updater is available."""
        return self._available

    @property
    def hass(self) -> HomeAssistant:
        """Return Home Assistant instance."""
        return self._hass

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create client session."""
        if not self._session or self._session.closed:
            self._session = aiohttp_client.async_get_clientsession(self._hass)
        return self._session

    def register_entity(self, entity) -> None:
        """Register an entity for updates."""
        if entity not in self._entities:
            self._entities.add(entity)

    def register_update_callback(self, callback_fn: callback) -> None:
        """Register callback for data updates."""
        if callback_fn not in self._entity_update_callbacks:
            self._entity_update_callbacks.append(callback_fn)
    
    def unregister_update_callback(self, callback_fn: callback) -> None:
        """Unregister callback for data updates."""
        if callback_fn in self._entity_update_callbacks:
            self._entity_update_callbacks.remove(callback_fn)

    def notify_entities(self) -> None:
        """Notify all registered entities of data update."""
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
        
        for callback_fn in self._entity_update_callbacks:
            try:
                callback_fn()
            except Exception as err:
                _LOGGER.error("Error in update callback: %s", str(err))

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
                
                try:
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
                        self.notify_entities()
                except ValueError as err:
                    _LOGGER.error("Error parsing JSON: %s", err)
                    self._network.update_metrics(
                        response_time=time.time() - start_time,
                        success=False,
                        error_type="JSONDecodeError"
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
                if self._data != cached_state:
                    self._data = cached_state
                    self.notify_entities()
            else:
                self._available = False
                self.notify_entities()
            
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
