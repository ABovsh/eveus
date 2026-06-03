"""Config flow for Eveus."""
from __future__ import annotations

import logging
import asyncio
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util.network import is_host_valid, is_ip_address

from .const import (
    DOMAIN,
    MODEL_16A,
    CONF_MODEL,
    CONF_SCHEME,
    DEFAULT_SCHEME,
    MODELS,
    CONF_PHASES,
    DEFAULT_PHASES,
    PHASE_OPTIONS,
    CONF_SOC_MODE,
    SOC_MODE_ADVANCED,
    SOC_MODE_OPTIONS,
    CONF_INITIAL_SOC,
    CONF_TARGET_SOC,
    CONF_BATTERY_CAPACITY,
    CONF_SOC_CORRECTION,
    DEFAULT_INITIAL_SOC,
    DEFAULT_TARGET_SOC,
    DEFAULT_BATTERY_CAPACITY,
    DEFAULT_SOC_CORRECTION,
    SOC_INPUT_LIMITS,
    get_soc_mode,
)
from ._payload import PayloadError, validate_main_payload
from .utils import normalize_soc_input
from . import CONFIG_ENTRY_VERSION

_LOGGER = logging.getLogger(__name__)

# Keys outside the user-editable form that must survive reconfigure/reauth/repair.
_PRESERVED_ENTRY_KEYS: tuple[str, ...] = (
    "device_number",
    CONF_INITIAL_SOC,
    CONF_TARGET_SOC,
    CONF_BATTERY_CAPACITY,
    CONF_SOC_CORRECTION,
)

_SOC_MODE_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=SOC_MODE_OPTIONS,
        translation_key="soc_mode",
        mode=SelectSelectorMode.DROPDOWN,
    )
)


def _prefill_from_helper(hass, entity_id: str, key: str, fallback: float) -> float:
    """Read a current input_number.* state for prefill, else fallback.

    Routes through normalize_soc_input so a non-finite or out-of-range helper
    state is clamped/rejected rather than seeding the form with a bad default.
    """
    state = hass.states.get(entity_id)
    if state is None:
        return fallback
    return normalize_soc_input(key, state.state, fallback)


def build_soc_step_schema(hass) -> vol.Schema:
    """Build the advanced-mode SOC value step, prefilled from any ev_* helpers."""
    cap_lo, cap_hi = SOC_INPUT_LIMITS["battery_capacity"]
    cor_lo, cor_hi = SOC_INPUT_LIMITS["soc_correction"]
    cap_default = _prefill_from_helper(
        hass, "input_number.ev_battery_capacity", "battery_capacity", DEFAULT_BATTERY_CAPACITY
    )
    cor_default = _prefill_from_helper(
        hass, "input_number.ev_soc_correction", "soc_correction", DEFAULT_SOC_CORRECTION
    )
    return vol.Schema(
        {
            vol.Required(
                CONF_BATTERY_CAPACITY, default=cap_default
            ): vol.All(vol.Coerce(float), vol.Range(min=cap_lo, max=cap_hi)),
            vol.Required(
                CONF_SOC_CORRECTION, default=cor_default
            ): vol.All(vol.Coerce(float), vol.Range(min=cor_lo, max=cor_hi)),
        }
    )


def _merge_entry_data(
    existing: Mapping[str, Any],
    incoming: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge form-derived data into existing entry.data without dropping runtime keys.

    Reconfigure/reauth flows return only the fields the user just edited. Replacing
    entry.data wholesale would discard integration-owned keys (e.g. device_number)
    and break entity unique-id stability on reload.
    """
    merged = dict(incoming)
    for key in _PRESERVED_ENTRY_KEYS:
        if key in existing and key not in merged:
            merged[key] = existing[key]
    return merged


def _warn_if_plaintext(scheme: str | None) -> None:
    """Warn once when credentials will be sent over plain HTTP."""
    if scheme == "http":
        _LOGGER.warning(
            "Eveus is configured over HTTP; Basic Auth credentials will be "
            "sent in cleartext on every poll. Use HTTPS on a LAN-trusted "
            "network or accept the exposure risk."
        )


def _host_is_valid(host: str) -> bool:
    """Accept a literal IP or a syntactically valid hostname."""
    host = (host or "").strip()
    if not host:
        return False
    return is_ip_address(host) or is_host_valid(host)


def _split_host_and_scheme(
    raw_host: str,
    default_scheme: str = DEFAULT_SCHEME,
) -> tuple[str, str]:
    """Validate host input and return normalized host[:port] plus scheme."""
    raw_host = raw_host.strip()
    if not raw_host:
        raise vol.Invalid("Host cannot be empty")

    # A bare IPv6 literal (2+ colons, no brackets, no scheme) confuses urlparse,
    # which reads the trailing group as a port. Bracket it so it parses as a host.
    if "://" not in raw_host and "[" not in raw_host and raw_host.count(":") >= 2:
        raw_host = f"[{raw_host}]"

    parsed = urlparse(raw_host if "://" in raw_host else f"//{raw_host}")
    scheme = parsed.scheme or default_scheme
    if scheme not in ("http", "https"):
        raise vol.Invalid("Unsupported URL scheme")

    if parsed.username or parsed.password:
        raise vol.Invalid("Credentials in URL are not allowed")

    if parsed.query or parsed.fragment:
        raise vol.Invalid("URL must not include a query or fragment")

    if parsed.path not in ("", "/"):
        raise vol.Invalid("URL must not include a path")

    hostname = parsed.hostname
    if not hostname:
        raise vol.Invalid("Invalid IP address or hostname")

    try:
        port = parsed.port
    except ValueError as err:
        raise vol.Invalid("Invalid port") from err

    if port is not None and not 1 <= port <= 65535:
        raise vol.Invalid("Invalid port")

    if not _host_is_valid(hostname):
        raise vol.Invalid("Invalid IP address or hostname")

    if hostname.endswith("."):
        hostname = hostname[:-1]

    if not is_ip_address(hostname):
        hostname = hostname.lower()

    is_ipv6 = ":" in hostname
    if is_ipv6 and not hostname.startswith("["):
        hostname = f"[{hostname}]"

    if port is not None:
        return f"{hostname}:{port}", scheme
    return hostname, scheme


def validate_host(host: str) -> str:
    """Validate host input."""
    return _split_host_and_scheme(host)[0]


def validate_credentials(username: str, password: str) -> tuple[str, str]:
    """Validate credentials input."""
    if not isinstance(username, str) or not isinstance(password, str):
        raise vol.Invalid("Username and password must be strings")
    username = username.strip()

    if not username or not password:
        raise vol.Invalid("Username and password cannot be empty")
    if len(username) > 32 or len(password) > 32:
        raise vol.Invalid("Username and password must be less than 32 characters")
    if ":" in username:
        raise vol.Invalid("Username cannot contain ':'")

    return username, password


def validate_device_response(
    result: Any,
    model: str,
) -> dict[str, Any]:
    """Validate that /main returned an Eveus-compatible payload."""
    try:
        payload = validate_main_payload(result, model, message_style="config_flow")
    except PayloadError as err:
        if err.code == "not_dict":
            raise CannotConnect(str(err)) from err
        raise InvalidDevice(str(err)) from err

    return {
        "current_set": float(payload["currentSet"]),
        "firmware": payload.get("verFWMain", "Unknown"),
    }


def normalize_user_input(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize config-flow input before connection validation and storage."""
    host, scheme = _split_host_and_scheme(
        data[CONF_HOST],
        data.get(CONF_SCHEME, DEFAULT_SCHEME),
    )
    username, password = validate_credentials(data[CONF_USERNAME], data[CONF_PASSWORD])
    model = data.get(CONF_MODEL)
    if model not in MODELS:
        raise vol.Invalid("Invalid charger model")

    raw_phases = data.get(CONF_PHASES, DEFAULT_PHASES)
    invalid_phase_count = "Invalid phase count"
    if isinstance(raw_phases, bool):
        raise vol.Invalid(invalid_phase_count)
    try:
        phases = int(raw_phases)
    except (TypeError, ValueError, OverflowError) as err:
        raise vol.Invalid(invalid_phase_count) from err
    if phases not in PHASE_OPTIONS:
        raise vol.Invalid(invalid_phase_count)

    soc_mode = data.get(CONF_SOC_MODE, SOC_MODE_ADVANCED)
    if soc_mode not in SOC_MODE_OPTIONS:
        raise vol.Invalid("Invalid SOC mode")

    return {
        CONF_HOST: host,
        CONF_USERNAME: username,
        CONF_PASSWORD: password,
        CONF_MODEL: model,
        CONF_SCHEME: scheme,
        CONF_PHASES: phases,
        CONF_SOC_MODE: soc_mode,
    }


def _safe_phases_default(raw: Any) -> int:
    """Coerce stored phase data to a valid option, falling back on DEFAULT_PHASES."""
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_PHASES
    return value if value in PHASE_OPTIONS else DEFAULT_PHASES


def build_user_data_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Build config-flow schema with optional defaults."""
    defaults = defaults or {}
    host_default = defaults.get(CONF_HOST)
    if host_default and defaults.get(CONF_SCHEME) == "https":
        host_default = f"https://{host_default}"
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=host_default): str,
            vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME)): str,
            vol.Required(CONF_PASSWORD): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
            vol.Required(
                CONF_MODEL,
                default=defaults.get(CONF_MODEL, MODEL_16A),
            ): vol.In(MODELS),
            vol.Required(
                CONF_PHASES,
                default=_safe_phases_default(defaults.get(CONF_PHASES)),
            ): vol.In(PHASE_OPTIONS),
            vol.Required(
                CONF_SOC_MODE,
                default=defaults.get(CONF_SOC_MODE, SOC_MODE_ADVANCED),
            ): _SOC_MODE_SELECTOR,
        }
    )


STEP_USER_DATA_SCHEMA = build_user_data_schema()


def build_reauth_data_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    """Build reauth schema for credential updates."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME)): str,
            vol.Required(CONF_PASSWORD): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
        }
    )


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    try:
        normalized_data = normalize_user_input(data)
    except vol.Invalid as err:
        raise InvalidInput(str(err))

    # Warn before connecting: credentials are sent in cleartext over HTTP on the
    # very first request, so the warning must fire even when the attempt fails
    # and for every flow that validates (setup, reconfigure, reauth, repair).
    _warn_if_plaintext(normalized_data[CONF_SCHEME])

    try:
        session = aiohttp_client.async_get_clientsession(hass)
        timeout = aiohttp.ClientTimeout(total=10)

        async with session.post(
            f"{normalized_data[CONF_SCHEME]}://{normalized_data[CONF_HOST]}/main",
            auth=aiohttp.BasicAuth(
                normalized_data[CONF_USERNAME],
                normalized_data[CONF_PASSWORD],
            ),
            timeout=timeout,
        ) as response:
            if response.status == 401:
                raise InvalidAuth("Invalid credentials")
            response.raise_for_status()

            try:
                result = await response.json(content_type=None)
            except ValueError:
                raise CannotConnect("Invalid response format")

            device_info = validate_device_response(result, normalized_data[CONF_MODEL])

            return {
                "title": f"Eveus Charger ({normalized_data[CONF_HOST]})",
                "data": normalized_data,
                "device_info": device_info,
            }

    except aiohttp.ClientResponseError as err:
        if err.status == 401:
            raise InvalidAuth from err
        raise CannotConnect(f"Connection error: {type(err).__name__}") from err
    except (asyncio.TimeoutError, aiohttp.ClientError) as err:
        raise CannotConnect(f"Connection error: {type(err).__name__}") from err
    except (InvalidAuth, InvalidDevice, InvalidInput, CannotConnect):
        raise
    except Exception as err:
        _LOGGER.exception("Unexpected Eveus setup error")
        raise CannotConnect(f"Unexpected error: {type(err).__name__}") from err


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Eveus."""

    VERSION = CONFIG_ENTRY_VERSION

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._host: str | None = None
        self._pending_entry: dict[str, Any] | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)

                entry_data = info["data"]
                self._host = entry_data[CONF_HOST]
                await self.async_set_unique_id(self._host)
                self._abort_if_unique_id_configured()


                self._pending_entry = {
                    "title": info["title"],
                    "data": entry_data,
                }
                if entry_data.get(CONF_SOC_MODE) == SOC_MODE_ADVANCED:
                    return await self.async_step_soc()
                return self._finish_entry()

            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except InvalidInput as err:
                errors["base"] = "invalid_input"
                _LOGGER.debug("Invalid input: %s", str(err))
            except InvalidDevice as err:
                errors["base"] = "invalid_device"
                _LOGGER.debug("Invalid device: %s", str(err))
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    def _finish_entry(self) -> FlowResult:
        """Create the entry from the pending payload, seeding advanced defaults."""
        data = dict(self._pending_entry["data"])
        if data.get(CONF_SOC_MODE) == SOC_MODE_ADVANCED:
            data.setdefault(CONF_INITIAL_SOC, DEFAULT_INITIAL_SOC)
            data.setdefault(CONF_TARGET_SOC, DEFAULT_TARGET_SOC)
        return self.async_create_entry(
            title=self._pending_entry["title"], data=data
        )

    async def async_step_soc(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Collect the set-once SOC values for advanced mode."""
        if user_input is not None:
            self._pending_entry["data"].update(
                {
                    CONF_BATTERY_CAPACITY: user_input[CONF_BATTERY_CAPACITY],
                    CONF_SOC_CORRECTION: user_input[CONF_SOC_CORRECTION],
                }
            )
            return self._finish_entry()
        return self.async_show_form(
            step_id="soc",
            data_schema=build_soc_step_schema(self.hass),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> "EveusOptionsFlow":
        """Return the options flow handler for toggling SOC mode."""
        return EveusOptionsFlow(config_entry)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Allow users to reconfigure connection details."""
        entry = self._get_reconfigure_entry()
        errors = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
                entry_data = _merge_entry_data(entry.data, info["data"])

                await self.async_set_unique_id(entry_data[CONF_HOST])
                if entry.unique_id != entry_data[CONF_HOST]:
                    self._abort_if_unique_id_configured()


                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=entry_data[CONF_HOST],
                    title=info["title"],
                    data=entry_data,
                )

            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except InvalidInput as err:
                errors["base"] = "invalid_input"
                _LOGGER.debug("Invalid reconfigure input: %s", str(err))
            except InvalidDevice as err:
                errors["base"] = "invalid_device"
                _LOGGER.debug("Invalid reconfigure device: %s", str(err))
            except Exception:
                _LOGGER.exception("Unexpected reconfigure exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=build_user_data_schema(entry.data),
            errors=errors,
        )

    async def async_step_reauth(
        self,
        entry_data: Mapping[str, Any],
    ) -> FlowResult:
        """Handle reauthentication when stored credentials fail."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Update credentials for an existing Eveus charger."""
        entry = self._get_reauth_entry()
        errors = {}

        if user_input is not None:
            try:
                merged_data = dict(entry.data)
                merged_data[CONF_USERNAME] = user_input[CONF_USERNAME]
                merged_data[CONF_PASSWORD] = user_input[CONF_PASSWORD]

                info = await validate_input(self.hass, merged_data)
                entry_data = _merge_entry_data(entry.data, info["data"])

                await self.async_set_unique_id(entry_data[CONF_HOST])
                if entry.unique_id != entry_data[CONF_HOST]:
                    return self.async_abort(reason="wrong_device")


                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=entry_data[CONF_HOST],
                    title=info["title"],
                    data=entry_data,
                )

            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except InvalidInput as err:
                errors["base"] = "invalid_input"
                _LOGGER.debug("Invalid reauth input: %s", str(err))
            except InvalidDevice as err:
                errors["base"] = "invalid_device"
                _LOGGER.debug("Invalid reauth device: %s", str(err))
            except Exception:
                _LOGGER.exception("Unexpected reauth exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=build_reauth_data_schema(entry.data),
            errors=errors,
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect to the device."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid authentication."""


class InvalidInput(HomeAssistantError):
    """Error to indicate invalid user input."""


class InvalidDevice(HomeAssistantError):
    """Error to indicate invalid device response or capabilities."""


class EveusOptionsFlow(OptionsFlow):
    """Toggle the integration mode (Basic / Advanced) after setup."""

    def __init__(self, entry) -> None:
        """Store the config entry being edited."""
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show and apply the SOC mode chooser."""
        if user_input is not None:
            new_data = dict(self._entry.data)
            new_data[CONF_SOC_MODE] = user_input[CONF_SOC_MODE]
            self.hass.config_entries.async_update_entry(self._entry, data=new_data)
            return self.async_create_entry(title="", data={})
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SOC_MODE, default=get_soc_mode(self._entry)
                ): _SOC_MODE_SELECTOR,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
