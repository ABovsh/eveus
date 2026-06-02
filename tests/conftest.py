"""Shared test helpers and lightweight dependency shims."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

TEST_HOST = "192.168.1.50"  # NOSONAR(python:S1313) - RFC 1918 LAN address, test fixture only
TEST_HOST_ALT = "192.168.1.55"  # NOSONAR(python:S1313) - RFC 1918 LAN address, test fixture only
TEST_BASE_URL = f"http://{TEST_HOST}"  # NOSONAR(python:S5332) - HTTP required by charger firmware; test fixture only
TEST_BASE_URL_ALT = f"http://{TEST_HOST_ALT}"  # NOSONAR(python:S5332) - HTTP required by charger firmware; test fixture only
TEST_USERNAME = "test_user"  # NOSONAR(python:S2068) - test fixture, not a real credential
TEST_PASSWORD = "test_password"  # NOSONAR(python:S2068) - test fixture, not a real credential
EV_HELPERS = {
    "input_number.ev_initial_soc": 20,
    "input_number.ev_battery_capacity": 80,
    "input_number.ev_soc_correction": 10,
    "input_number.ev_target_soc": 80,
}

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_voluptuous_stub() -> None:
    if importlib.util.find_spec("voluptuous") is not None:
        return

    vol = types.ModuleType("voluptuous")

    class Invalid(Exception):
        """Test replacement for voluptuous.Invalid."""

    class Schema:
        def __init__(self, schema_: Any, *args: Any, **kwargs: Any) -> None:
            self.schema_ = schema_

        def __call__(self, value: Any) -> Any:
            return value

    def required_(key: str, default: Any = None) -> str:
        return key

    def in_(values: Any) -> Any:
        return values

    vol.Invalid = Invalid
    vol.Schema = Schema
    vol.Required = required_
    vol.In = in_
    vol.ALLOW_EXTRA = object()
    sys.modules["voluptuous"] = vol


def _install_aiohttp_stub() -> None:
    if importlib.util.find_spec("aiohttp") is not None:
        return

    aiohttp = types.ModuleType("aiohttp")

    class ClientError(Exception):
        """Base aiohttp client error."""

    class ClientResponseError(ClientError):
        def __init__(self, *args: Any, status: int | None = None, **kwargs: Any) -> None:
            super().__init__(*args)
            self.status = status

    class ClientConnectorError(ClientError):
        """Connection error."""

    class ClientSession:
        """Placeholder client session type."""

    class ClientTimeout:
        def __init__(self, *args: Any, total: float | None = None, **kwargs: Any) -> None:
            self.total = total

    class BasicAuth:
        def __init__(self, login: str, password: str = "") -> None:
            self.login = login
            self.password = password

    aiohttp.ClientError = ClientError
    aiohttp.ClientResponseError = ClientResponseError
    aiohttp.ClientConnectorError = ClientConnectorError
    aiohttp.ClientSession = ClientSession
    aiohttp.ClientTimeout = ClientTimeout
    aiohttp.BasicAuth = BasicAuth
    sys.modules["aiohttp"] = aiohttp


def _install_homeassistant_stub() -> None:
    if importlib.util.find_spec("homeassistant") is not None:
        return

    ha = types.ModuleType("homeassistant")
    sys.modules["homeassistant"] = ha

    const = types.ModuleType("homeassistant.const")
    const.CONF_HOST = "host"
    const.CONF_USERNAME = "username"
    const.CONF_PASSWORD = "password"

    class Platform:
        SENSOR = "sensor"
        SWITCH = "switch"
        NUMBER = "number"
        BINARY_SENSOR = "binary_sensor"
        BUTTON = "button"
        SELECT = "select"
        TIME = "time"

    class UnitOfEnergy:
        KILO_WATT_HOUR = "kWh"

    class UnitOfElectricCurrent:
        AMPERE = "A"

    class UnitOfElectricPotential:
        VOLT = "V"

    class UnitOfPower:
        WATT = "W"

    class UnitOfTemperature:
        CELSIUS = "°C"

    const.Platform = Platform
    const.UnitOfEnergy = UnitOfEnergy
    const.UnitOfElectricCurrent = UnitOfElectricCurrent
    const.UnitOfElectricPotential = UnitOfElectricPotential
    const.UnitOfPower = UnitOfPower
    const.UnitOfTemperature = UnitOfTemperature
    sys.modules["homeassistant.const"] = const

    core = types.ModuleType("homeassistant.core")

    class State:
        def __init__(self, *args: str) -> None:
            self.state_ = args[-1]

    class HomeAssistant:
        """Placeholder Home Assistant object."""

    def callback(func: Any) -> Any:
        return func

    core.State = State
    core.HomeAssistant = HomeAssistant
    core.callback = callback
    sys.modules["homeassistant.core"] = core

    exceptions = types.ModuleType("homeassistant.exceptions")

    class HomeAssistantError(Exception):
        """Base Home Assistant error."""

    class ConfigEntryNotReady(HomeAssistantError):
        """Setup should be retried later."""

    class ConfigEntryAuthFailed(HomeAssistantError):
        """Authentication failed."""

    class ConfigEntryError(HomeAssistantError):
        """Unrecoverable config entry setup error."""

    exceptions.HomeAssistantError = HomeAssistantError
    exceptions.ConfigEntryNotReady = ConfigEntryNotReady
    exceptions.ConfigEntryAuthFailed = ConfigEntryAuthFailed
    exceptions.ConfigEntryError = ConfigEntryError
    sys.modules["homeassistant.exceptions"] = exceptions

    data_entry_flow = types.ModuleType("homeassistant.data_entry_flow")
    data_entry_flow.FlowResult = dict[str, Any]
    sys.modules["homeassistant.data_entry_flow"] = data_entry_flow

    config_entries = types.ModuleType("homeassistant.config_entries")

    class ConfigEntry:
        def __class_getitem__(cls, item: Any) -> Any:
            return cls

        def __init__(self, data: dict[str, Any] | None = None, title: str = "Eveus") -> None:
            self.data = data or {}
            self.title = title
            self.entry_id = "entry-id"

    class ConfigFlow:
        def __init_subclass__(cls, **kwargs: Any) -> None:
            super().__init_subclass__()

        async def async_set_unique_id(self, unique_id: str) -> None:
            self._unique_id = unique_id

        def _abort_if_unique_id_configured(self) -> None:
            return None

        def async_create_entry(self, *, title: str, data: dict[str, Any]) -> dict[str, Any]:
            return {"type": "create_entry", "title": title, "data": data}

        def async_show_form(
            self, *, step_id: str, data_schema: Any, errors: dict[str, str] | None = None
        ) -> dict[str, Any]:
            return {
                "type": "form",
                "step_id": step_id,
                "data_schema": data_schema,
                "errors": errors or {},
            }

    class OptionsFlow:
        def async_create_entry(self, *, title: str, data: dict[str, Any]) -> dict[str, Any]:
            return {"type": "create_entry", "title": title, "data": data}

        def async_show_form(self, *, step_id: str, data_schema: Any) -> dict[str, Any]:
            return {"type": "form", "step_id": step_id, "data_schema": data_schema}

    config_entries.ConfigEntry = ConfigEntry
    config_entries.ConfigFlow = ConfigFlow
    config_entries.OptionsFlow = OptionsFlow
    sys.modules["homeassistant.config_entries"] = config_entries
    ha.config_entries = config_entries

    helpers = types.ModuleType("homeassistant.helpers")
    sys.modules["homeassistant.helpers"] = helpers

    issue_registry = types.ModuleType("homeassistant.helpers.issue_registry")

    class IssueSeverity:
        ERROR = "error"
        WARNING = "warning"

    def async_create_issue(hass: Any, domain: str, issue_id: str, **kwargs: Any) -> None:
        hass.issues = getattr(hass, "issues", {})
        hass.issues[(domain, issue_id)] = kwargs

    def async_delete_issue(hass: Any, domain: str, issue_id: str) -> None:
        hass.issues = getattr(hass, "issues", {})
        hass.issues.pop((domain, issue_id), None)

    issue_registry.IssueSeverity = IssueSeverity
    issue_registry.async_create_issue = async_create_issue
    issue_registry.async_delete_issue = async_delete_issue
    sys.modules["homeassistant.helpers.issue_registry"] = issue_registry
    helpers.issue_registry = issue_registry

    aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")

    def async_get_clientsession(hass: Any) -> Any:
        return hass.session

    aiohttp_client.async_get_clientsession = async_get_clientsession
    sys.modules["homeassistant.helpers.aiohttp_client"] = aiohttp_client
    helpers.aiohttp_client = aiohttp_client

    typing_mod = types.ModuleType("homeassistant.helpers.typing")
    typing_mod.ConfigType = dict[str, Any]
    sys.modules["homeassistant.helpers.typing"] = typing_mod

    entity = types.ModuleType("homeassistant.helpers.entity")

    class EntityCategory:
        CONFIG = "config"
        DIAGNOSTIC = "diagnostic"

    entity.EntityCategory = EntityCategory
    sys.modules["homeassistant.helpers.entity"] = entity

    restore_state = types.ModuleType("homeassistant.helpers.restore_state")

    class RestoreEntity:
        async def async_added_to_hass(self) -> None:
            return None

    restore_state.RestoreEntity = RestoreEntity
    sys.modules["homeassistant.helpers.restore_state"] = restore_state

    entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    entity_platform.AddEntitiesCallback = Any
    sys.modules["homeassistant.helpers.entity_platform"] = entity_platform

    event = types.ModuleType("homeassistant.helpers.event")

    def async_track_state_change_event(*args: Any, **kwargs: Any) -> Any:
        return lambda: None

    event.async_track_state_change_event = async_track_state_change_event
    sys.modules["homeassistant.helpers.event"] = event

    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")

    class UpdateFailed(Exception):
        """Raised when a coordinator update fails."""

    class DataUpdateCoordinator:
        def __class_getitem__(cls, item: Any) -> Any:
            return cls

        def __init__(self, hass: Any = None, logger: Any = None, *, name: str = "", update_interval: Any = None) -> None:
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.data: Any = None
            self.last_update_success = True

        def async_add_listener(self, *args: Any, **kwargs: Any) -> Any:
            return lambda: None

        async def async_config_entry_first_refresh(self) -> None:
            return None

        async def async_request_refresh(self) -> None:
            return None

        def async_set_updated_data(self, data: Any) -> None:
            self.data = data

    class CoordinatorEntity:
        def __init__(self, coordinator: Any = None) -> None:
            self.coordinator = coordinator

        async def async_added_to_hass(self) -> None:
            return None

    update_coordinator.UpdateFailed = UpdateFailed
    update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    update_coordinator.CoordinatorEntity = CoordinatorEntity
    sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator
    helpers.update_coordinator = update_coordinator

    components = types.ModuleType("homeassistant.components")
    sys.modules["homeassistant.components"] = components

    diagnostics = types.ModuleType("homeassistant.components.diagnostics")

    def async_redact_data(data: dict[str, Any], to_redact: set[str]) -> dict[str, Any]:
        return {
            key: "**REDACTED**" if key in to_redact else value
            for key, value in data.items()
        }

    diagnostics.async_redact_data = async_redact_data
    sys.modules["homeassistant.components.diagnostics"] = diagnostics

    repairs = types.ModuleType("homeassistant.components.repairs")

    class RepairsFlow:
        def async_create_entry(self, *, title: str, data: dict[str, Any]) -> dict[str, Any]:
            return {"type": "create_entry", "title": title, "data": data}

        def async_show_form(
            self,
            *,
            step_id: str,
            data_schema: Any,
            errors: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            return {
                "type": "form",
                "step_id": step_id,
                "data_schema": data_schema,
                "errors": errors or {},
            }

        def async_abort(self, *, reason: str) -> dict[str, Any]:
            return {"type": "abort", "reason": reason}

    repairs.RepairsFlow = RepairsFlow
    sys.modules["homeassistant.components.repairs"] = repairs

    sensor = types.ModuleType("homeassistant.components.sensor")

    class SensorEntity:
        """Placeholder sensor entity."""

    class SensorDeviceClass:
        BATTERY = "battery"
        CURRENT = "current"
        ENERGY = "energy"
        POWER = "power"
        TEMPERATURE = "temperature"
        VOLTAGE = "voltage"

    class SensorStateClass:
        MEASUREMENT = "measurement"
        TOTAL = "total"
        TOTAL_INCREASING = "total_increasing"

    sensor.SensorEntity = SensorEntity
    sensor.SensorDeviceClass = SensorDeviceClass
    sensor.SensorStateClass = SensorStateClass
    sys.modules["homeassistant.components.sensor"] = sensor

    binary_sensor = types.ModuleType("homeassistant.components.binary_sensor")

    class BinarySensorEntity:
        """Placeholder binary sensor entity."""

    class BinarySensorDeviceClass:
        CONNECTIVITY = "connectivity"
        PROBLEM = "problem"
        POWER = "power"
        RUNNING = "running"
        PLUG = "plug"

    binary_sensor.BinarySensorEntity = BinarySensorEntity
    binary_sensor.BinarySensorDeviceClass = BinarySensorDeviceClass
    sys.modules["homeassistant.components.binary_sensor"] = binary_sensor

    switch = types.ModuleType("homeassistant.components.switch")

    class SwitchEntity:
        """Placeholder switch entity."""

    switch.SwitchEntity = SwitchEntity
    sys.modules["homeassistant.components.switch"] = switch

    number = types.ModuleType("homeassistant.components.number")

    class NumberEntity:
        """Placeholder number entity."""

    class NumberMode:
        SLIDER = "slider"

    class NumberDeviceClass:
        CURRENT = "current"

    number.NumberEntity = NumberEntity
    number.NumberMode = NumberMode
    number.NumberDeviceClass = NumberDeviceClass
    sys.modules["homeassistant.components.number"] = number


_install_voluptuous_stub()
_install_aiohttp_stub()
_install_homeassistant_stub()


class HelperStates:
    """Small Home Assistant state registry fake for helper-entity tests."""

    def __init__(self, values: dict[str, object] | None = None) -> None:
        self._values = values or {}
        self.calls: list[str] = []

    def get(self, entity_id: str) -> SimpleNamespace | None:
        self.calls.append(entity_id)
        value = self._values.get(entity_id)
        if value is None:
            return None
        return SimpleNamespace(state=str(value))


class HelperHass:
    """Minimal hass object exposing only the states API used by helper sensors."""

    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.states = HelperStates(values)


class EveusTestUpdater:
    """Reusable coordinator/updater fake for direct entity tests."""

    host = TEST_HOST
    available = True
    last_update_success = True
    scheme = "http"

    def __init__(
        self,
        data: dict[str, object] | None = None,
        *,
        host: str = TEST_HOST,
        available: bool = True,
        scheme: str = "http",
        quality: dict[str, object] | None = None,
    ) -> None:
        self.host = host
        self.available = available
        self.scheme = scheme
        self.data = data or {}
        self.connection_quality = quality or {}
        self.commands: list[tuple[str, object]] = []
        self.command_extras: list[dict[str, object] | None] = []
        self.command_result = True
        self.config_entry = SimpleNamespace(entry_id="entry-id")

    def async_add_listener(self, *args: object, **kwargs: object):
        return lambda: None

    async def send_command(
        self,
        command: str,
        value: object,
        *,
        retry: bool = True,
        extra: dict[str, object] | None = None,
    ) -> bool:
        self.commands.append((command, value))
        self.command_extras.append(extra)
        self.last_retry = retry
        self.last_extra = extra
        return self.command_result


def disable_state_writes(entity: object) -> None:
    """Replace HA state writes with a no-op for direct entity unit tests."""
    entity.async_write_ha_state = lambda: None
