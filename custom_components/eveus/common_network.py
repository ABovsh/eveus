"""Optimized network handling with silent offline mode - no noise when charger is turned off."""
import logging
import asyncio
import time
import json
from typing import Any, Optional, Set, Dict, Callable, List
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

import aiohttp
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import aiohttp_client

from .const import (
    CHARGING_UPDATE_INTERVAL,
    IDLE_UPDATE_INTERVAL,
    RETRY_DELAY,
    UPDATE_TIMEOUT,
    ERROR_COOLDOWN,
    COMMAND_TIMEOUT,
    ERROR_LOG_RATE_LIMIT,
    STATE_CACHE_TTL,
)
from .utils import get_safe_value
from .common_command import CommandManager

_LOGGER = logging.getLogger(__name__)

class ConnectionState(Enum):
    """Connection state enumeration."""
    CONNECTED = "connected"
    CONNECTING = "connecting"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    OFFLINE = "offline"

@dataclass
class NetworkMetrics:
    """Lightweight network metrics with offline handling."""
    success_count: int = 0
    total_count: int = 0
    avg_latency: float = 0.0
    last_success_time: float = field(default_factory=time.time)
    last_error_type: Optional[str] = None
    last_error_time: float = 0.0
    consecutive_failures: int = 0
    is_expected_offline: bool = False
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate percentage."""
        return (self.success_count / max(self.total_count, 1)) * 100
    
    @property
    def is_healthy(self) -> bool:
        """Check if connection is healthy."""
        return self.success_rate > 80 and time.time() - self.last_success_time < 300
    
    @property
    def is_likely_offline(self) -> bool:
        """Check if device is likely offline (not temporary network issue)."""
        return (self.consecutive_failures > 10 and 
                time.time() - self.last_success_time > 600)

class NetworkManager:
    """Memory-optimized network manager with silent offline handling."""
    
    def __init__(self, host: str, window_size: int = 20) -> None:
        """Initialize optimized network manager."""
        self.host = host
        self._window_size = window_size
        self._metrics = NetworkMetrics()
        
        # Sliding window for latency (much smaller than before)
        self._latency_window = deque(maxlen=10)
        
        # Minimal error tracking
        self._recent_errors = deque(maxlen=3)
        
        # Connection state
        self._state = ConnectionState.DISCONNECTED
        self._cached_state: Optional[Dict[str, Any]] = None
        self._cached_state_time: float = 0
        
        # Silent mode tracking
        self._last_offline_log = 0
        self._silent_mode = False
        self._offline_announced = False
        
    @property
    def connection_quality(self) -> Dict[str, Any]:
        """Get lightweight connection quality metrics."""
        current_time = time.time()
        
        return {
            'success_rate': self._metrics.success_rate,
            'latency_avg': self._metrics.avg_latency,
            'recent_errors': len(self._recent_errors),
            'last_successful_connection': self._metrics.last_success_time,
            'is_healthy': self._metrics.is_healthy,
            'state': self._state.value,
            'uptime': current_time - self._metrics.last_success_time if self._metrics.last_success_time else 0,
            'consecutive_failures': self._metrics.consecutive_failures,
            'is_likely_offline': self._metrics.is_likely_offline
        }
    
    def update_metrics(self, response_time: float, success: bool, error_type: str = None) -> None:
        """Update metrics efficiently with silent offline detection."""
        self._metrics.total_count += 1
        
        if success:
            self._metrics.success_count += 1
            self._metrics.last_success_time = time.time()
            self._metrics.consecutive_failures = 0
            self._metrics.is_expected_offline = False
            self._latency_window.append(response_time)
            self._state = ConnectionState.CONNECTED
            self._silent_mode = False
            self._offline_announced = False
            
            # Update average latency efficiently
            if self._latency_window:
                self._metrics.avg_latency = sum(self._latency_window) / len(self._latency_window)
        else:
            self._metrics.consecutive_failures += 1
            self._metrics.last_error_type = error_type
            self._metrics.last_error_time = time.time()
            
            # Enter silent mode after device is clearly offline
            if self._metrics.consecutive_failures > 20:
                self._silent_mode = True
                self._metrics.is_expected_offline = True
            
            # Determine if device is likely offline vs temporary error
            if self._metrics.is_likely_offline:
                self._state = ConnectionState.OFFLINE
                self._metrics.is_expected_offline = True
            else:
                self._state = ConnectionState.ERROR
            
            # Only store essential error info when not in silent mode
            if error_type and not self._silent_mode:
                self._recent_errors.append({
                    'type': error_type,
                    'time': time.time()
                })
    
    def cache_state(self, state_data: Dict[str, Any]) -> None:
        """Cache state data efficiently."""
        self._cached_state = state_data
        self._cached_state_time = time.time()
    
    def get_cached_state(self) -> Optional[Dict[str, Any]]:
        """Get cached state if still valid."""
        if (self._cached_state and 
            time.time() - self._cached_state_time < STATE_CACHE_TTL):
            return self._cached_state
        return None
    
    def should_log_offline(self) -> bool:
        """Check if we should log offline status (rate limited and respects silent mode)."""
        if self._silent_mode:
            return False
            
        current_time = time.time()
        if current_time - self._last_offline_log > ERROR_LOG_RATE_LIMIT:
            self._last_offline_log = current_time
            return True
        return False
    
    def is_silent_mode(self) -> bool:
        """Check if in silent mode (device is offline for extended period)."""
        return self._silent_mode


class EveusUpdater:
    """High-performance updater with silent offline handling."""

    def __init__(self, host: str, username: str, password: str, hass: HomeAssistant) -> None:
        """Initialize optimized updater."""
        self.host = host
        self.username = username  
        self.password = password
        self._hass = hass
        
        # Optimized data management
        self._data: Dict[str, Any] = {}
        self._previous_data: Dict[str, Any] = {}
        self._available = True
        
        # Connection management
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_created_time = 0
        self._session_ttl = 3600  # 1 hour session TTL
        
        # Entity management (using sets for performance)
        self._entities: Set[Any] = set()
        self._update_callbacks: List[Callable] = []
        
        # Task management
        self._update_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()
        
        # Optimized components
        self._command_manager = CommandManager(self)
        self._network = NetworkManager(host)
        
        # Performance tracking
        self._update_count = 0
        self._last_significant_change = 0
        
        # Rate limited logging
        self._last_availability_log = 0

    @property
    def data(self) -> Dict[str, Any]:
        """Return current data (avoid copying for performance)."""
        return self._data

    @property
    def available(self) -> bool:
        """Return availability status."""
        return self._available

    @property
    def hass(self) -> HomeAssistant:
        """Return Home Assistant instance."""
        return self._hass

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create optimized client session with connection pooling."""
        current_time = time.time()
        
        if (not self._session or 
            self._session.closed or 
            current_time - self._session_created_time > self._session_ttl):
            
            if self._session and not self._session.closed:
                await self._session.close()
            
            timeout = aiohttp.ClientTimeout(total=UPDATE_TIMEOUT, connect=10)
            connector = aiohttp.TCPConnector(
                limit=10,
                ttl_dns_cache=300,
                use_dns_cache=True,
                keepalive_timeout=60,
                enable_cleanup_closed=True,
                force_close=False,
                limit_per_host=2
            )
            
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers={"Connection": "keep-alive", "User-Agent": "Eveus-Integration/1.0"}
            )
            self._session_created_time = current_time
            
        return self._session

    def register_entity(self, entity) -> None:
        """Register entity efficiently."""
        self._entities.add(entity)

    def register_update_callback(self, callback: Callable) -> None:
        """Register update callback."""
        if callback not in self._update_callbacks:
            self._update_callbacks.append(callback)
    
    def unregister_update_callback(self, callback: Callable) -> None:
        """Unregister update callback."""
        if callback in self._update_callbacks:
            self._update_callbacks.remove(callback)

    def notify_entities(self) -> None:
        """Notify entities of updates (optimized and silent)."""
        # Only notify if there are significant changes
        current_time = time.time()
        
        # Check for significant changes
        significant_keys = ['state', 'powerMeas', 'sessionEnergy', 'voltMeas1', 'curMeas1']
        has_significant_change = any(
            self._data.get(key) != self._previous_data.get(key) 
            for key in significant_keys
        )
        
        if has_significant_change or current_time - self._last_significant_change > 60:
            self._last_significant_change = current_time
            
            # Notify entities efficiently (suppress exceptions to reduce noise)
            for entity in self._entities:
                if hasattr(entity, 'hass') and entity.hass:
                    try:
                        entity.async_write_ha_state()
                    except Exception:
                        # Silently ignore entity update errors
                        pass
            
            # Execute callbacks (suppress exceptions to reduce noise)
            for callback in self._update_callbacks:
                try:
                    callback()
                except Exception:
                    # Silently ignore callback errors
                    pass

    async def send_command(self, command: str, value: Any) -> bool:
        """Send command using optimized manager."""
        return await self._command_manager.send_command(command, value)

    async def _update(self) -> None:
        """Optimized update method with silent offline handling."""
        start_time = time.time()
        
        try:
            session = await self._get_session()
            
            async with session.post(
                f"http://{self.host}/main",
                auth=aiohttp.BasicAuth(self.username, self.password),
                timeout=aiohttp.ClientTimeout(total=UPDATE_TIMEOUT),
            ) as response:
                response.raise_for_status()
                text = await response.text()
                
                # Parse and validate data
                try:
                    new_data = json.loads(text)
                    if not isinstance(new_data, dict):
                        raise ValueError(f"Invalid data type: {type(new_data)}")
                    
                    # Store previous data for change detection
                    self._previous_data = self._data.copy()
                    self._data = new_data
                    
                    # Update network metrics
                    response_time = time.time() - start_time
                    self._network.update_metrics(response_time, True)
                    self._network.cache_state(new_data)
                    
                    # Update availability (only log when coming back online)
                    if not self._available:
                        self._available = True
                        # Only log if not in silent mode
                        if not self._network.is_silent_mode():
                            _LOGGER.info("Connection restored to %s", self.host)
                    
                    # Notify entities of changes
                    self.notify_entities()
                    
                except (json.JSONDecodeError, ValueError) as err:
                    # Silent handling in offline mode
                    if not self._network.is_silent_mode():
                        _LOGGER.debug("Error parsing response from %s: %s", self.host, err)
                    self._network.update_metrics(time.time() - start_time, False, "ParseError")
                
        except aiohttp.ClientResponseError as err:
            self._handle_update_error(err, "HTTPError", start_time)
        except aiohttp.ClientConnectorError as err:
            self._handle_update_error(err, "ConnectionError", start_time)
        except asyncio.TimeoutError as err:
            self._handle_update_error(err, "TimeoutError", start_time)
        except Exception as err:
            self._handle_update_error(err, "UnknownError", start_time)

    def _handle_update_error(self, error: Exception, error_type: str, start_time: float) -> None:
        """Handle update errors with proper availability marking - CRITICAL FIX."""
        response_time = time.time() - start_time
        self._network.update_metrics(response_time, False, error_type)
        
        # CRITICAL FIX: Always mark as unavailable when there's an error
        # This ensures entities start their grace period timers correctly
        was_available = self._available
        self._available = False
        
        # Try to use cached state during temporary issues (but still mark unavailable)
        cached_state = self._network.get_cached_state()
        if cached_state and cached_state != self._data:
            # Use cached data temporarily, but updater is still unavailable
            if not self._network.is_silent_mode() and was_available:
                _LOGGER.debug("Using cached state during %s for %s (marked unavailable)", error_type, self.host)
            self._previous_data = self._data.copy()
            self._data = cached_state
            self.notify_entities()
        else:
            # No cached state - clear stale data and notify
            if was_available:
                # CRITICAL: Clear stale data when no cache available
                self._data = {}
                
                # Announce offline status only once, then go silent
                if self._network._metrics.is_likely_offline:
                    if not self._network._offline_announced:
                        _LOGGER.info("Device %s appears to be offline (turned off)", self.host)
                        self._network._offline_announced = True
                else:
                    # Only log temporary issues if not in silent mode
                    if not self._network.is_silent_mode() and self._should_log_availability():
                        _LOGGER.debug("Temporary connection issue with %s: %s", self.host, error_type)
                
                self.notify_entities()

    def _should_log_availability(self) -> bool:
        """Rate limit availability change logging."""
        current_time = time.time()
        if current_time - self._last_availability_log > ERROR_LOG_RATE_LIMIT:
            self._last_availability_log = current_time
            return True
        return False

    async def async_start_updates(self) -> None:
        """Start optimized update loop."""
        if self._update_task is None:
            self._shutdown_event.clear()
            self._update_task = asyncio.create_task(self._update_loop())
            await self._command_manager.start()
            _LOGGER.debug("Started optimized update loop for %s", self.host)

    async def _update_loop(self) -> None:
        """Optimized update loop with silent offline handling."""
        consecutive_failures = 0
        max_failures = 5
        
        while not self._shutdown_event.is_set():
            try:
                await self._update()
                consecutive_failures = 0
                
                # Adaptive polling interval
                is_charging = get_safe_value(self._data, "state", int) == 4
                is_active = get_safe_value(self._data, "powerMeas", float, 0) > 100
                
                if is_charging or is_active:
                    interval = CHARGING_UPDATE_INTERVAL
                else:
                    interval = IDLE_UPDATE_INTERVAL
                    
                await asyncio.sleep(interval)
                
            except asyncio.CancelledError:
                break
            except Exception as err:
                consecutive_failures += 1
                
                # Silent logging when device is offline
                if not self._network.is_silent_mode():
                    if consecutive_failures <= 3:
                        if self._should_log_availability():
                            _LOGGER.debug("Update loop error %d/%d for %s: %s", 
                                        consecutive_failures, max_failures, self.host, err)
                    elif consecutive_failures == 10:
                        _LOGGER.info("Device %s appears to be offline after multiple failures", self.host)
                
                # Exponential backoff with longer delays when offline
                if self._network._metrics.is_likely_offline:
                    backoff = min(RETRY_DELAY * 4, 300)  # Longer delays when offline
                else:
                    backoff = min(RETRY_DELAY * (2 ** (consecutive_failures - 1)), 60)
                
                jitter = backoff * 0.1
                delay = backoff + (jitter * (0.5 - asyncio.get_event_loop().time() % 1))
                
                await asyncio.sleep(delay)
                
                # Reset connection after too many failures (silently)
                if consecutive_failures >= max_failures:
                    if self._session and not self._session.closed:
                        await self._session.close()
                        self._session = None
                    consecutive_failures = 0

    async def async_shutdown(self) -> None:
        """Optimized shutdown process."""
        _LOGGER.debug("Shutting down updater for %s", self.host)
        
        # Signal shutdown
        self._shutdown_event.set()
        
        # Stop command manager
        await self._command_manager.stop()
        
        # Stop update loop
        if self._update_task:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass
            self._update_task = None
        
        # Close session
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
            
        _LOGGER.debug("Updater shutdown complete for %s", self.host)
