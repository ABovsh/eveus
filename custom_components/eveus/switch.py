async def _send_command(self, command: str, value: int) -> bool:
        """Send command to the device with improved error handling."""
        try:
            session = await self._get_session()
            async with session.post(
                f"http://{self._host}/pageEvent",
                auth=aiohttp.BasicAuth(self._username, self._password),
                headers={"Content-type": "application/x-www-form-urlencoded"},
                data=f"pageevent={command}&{command}={value}",
                timeout=10,
            ) as response:
                response.raise_for_status()
                self._available = True
                _LOGGER.debug(
                    "Successfully sent command %s=%s to %s",
                    command,
                    value,
                    self._host,
                )
                return True
        except aiohttp.ClientResponseError as error:
            self._available = False
            _LOGGER.error(
                "HTTP error sending command to %s: %s [status=%s]",
                self._host,
                error.message,
                error.status,
            )
            return False
        except aiohttp.ClientError as error:
            self._available = False
            _LOGGER.error(
                "Connection error sending command to %s: %s",
                self._host,
                str(error),
            )
            return False
        except Exception as error:
            self._available = False
            _LOGGER.error(
                "Unexpected error sending command to %s: %s",
                self._host,
                str(error),
            )
            return False

    async def _get_state(self, attribute: str) -> bool | None:
        """Get state from the device with improved validation."""
        try:
            session = await self._get_session()
            async with session.post(
                f"http://{self._host}/main",
                auth=aiohttp.BasicAuth(self._username, self._password),
                timeout=10,
            ) as response:
                response.raise_for_status()
                data = await response.json()
                
                if attribute not in data:
                    _LOGGER.warning(
                        "Attribute %s not found in response from %s",
                        attribute,
                        self._host,
                    )
                    return None
                
                value = data[attribute]
                if not isinstance(value, (int, float, str)):
                    _LOGGER.warning(
                        "Invalid value type for %s from %s: %s",
                        attribute,
                        self._host,
                        type(value),
                    )
                    return None
                
                self._available = True
                return value == 1
        except aiohttp.ClientResponseError as error:
            self._available = False
            _LOGGER.error(
                "HTTP error getting state from %s: %s [status=%s]",
                self._host,
                error.message,
                error.status,
            )
            return None
        except aiohttp.ClientError as error:
            self._available = False
            _LOGGER.error(
                "Connection error getting state from %s: %s",
                self._host,
                str(error),
            )
            return None
        except Exception as error:
            self._available = False
            _LOGGER.error(
                "Unexpected error getting state from %s: %s",
                self._host,
                str(error),
            )
            return None
