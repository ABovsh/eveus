"""Network utilities for Eveus integration."""
import logging
import aiohttp
import asyncio
from typing import Any, Optional, Dict, Union

from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client

_LOGGER = logging.getLogger(__name__)

async def get_session(hass: HomeAssistant) -> aiohttp.ClientSession:
    """Get or create client session."""
    return aiohttp_client.async_get_clientsession(hass)

async def send_http_request(
    hass: HomeAssistant, 
    method: str, 
    url: str, 
    auth: aiohttp.BasicAuth,
    data: Optional[Union[str, Dict[str, Any], bytes]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: Optional[int] = None
) -> str:
    """Send HTTP request with unified error handling."""
    session = await get_session(hass)
    timeout_obj = aiohttp.ClientTimeout(total=timeout) if timeout else None
    
    headers = headers or {}
    # Add keep-alive to improve connection efficiency
    headers["Connection"] = "keep-alive"
    
    try:
        async with session.request(
            method, 
            url,
            auth=auth,
            data=data,
            headers=headers,
            timeout=timeout_obj
        ) as response:
            response.raise_for_status()
            return await response.text()
    except aiohttp.ClientResponseError as err:
        _LOGGER.error("HTTP error %d: %s", err.status, err)
        raise
    except aiohttp.ClientConnectorError as err:
        _LOGGER.error("Connection error: %s", err)
        raise
    except asyncio.TimeoutError:
        _LOGGER.error("Request timed out")
        raise
    except Exception as err:
        _LOGGER.error("Unexpected error: %s", err)
        raise

async def fetch_data(
    hass: HomeAssistant, 
    host: str, 
    username: str, 
    password: str,
    endpoint: str = "main",
    timeout: Optional[int] = 20
) -> str:
    """Fetch data from device API."""
    url = f"http://{host}/{endpoint}"
    auth = aiohttp.BasicAuth(username, password)
    return await send_http_request(
        hass=hass,
        method="POST",
        url=url,
        auth=auth,
        timeout=timeout
    )

async def send_command(
    hass: HomeAssistant,
    host: str,
    username: str,
    password: str,
    command: str,
    value: Any,
    endpoint: str = "pageEvent",
    timeout: Optional[int] = 25
) -> bool:
    """Send command to device."""
    url = f"http://{host}/{endpoint}"
    auth = aiohttp.BasicAuth(username, password)
    
    headers = {"Content-type": "application/x-www-form-urlencoded"}
    data = f"pageevent={command}&{command}={value}"
    
    try:
        await send_http_request(
            hass=hass,
            method="POST",
            url=url,
            auth=auth,
            data=data,
            headers=headers,
            timeout=timeout
        )
        return True
    except Exception as err:
        _LOGGER.error("Failed to send command %s=%s: %s", command, value, err)
        return False
