"""Config flow for Eveus."""
from __future__ import annotations

import logging
import asyncio
import ipaddress
import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import AbortFlow, FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import aiohttp_client, device_registry as dr
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
    UPDATE_TIMEOUT,
    get_soc_mode,
)
from ._payload import PayloadError, read_body_capped
from .utils import normalize_soc_input
from . import CONFIG_ENTRY_VERSION

_LOGGER = logging.getLogger(__name__)

_INVALID_HOST_MSG = "Invalid IP address or hostname"
# Bound on reauth re-validations when a concurrent reconfigure keeps changing the
# host/scheme mid-flight; past this the flow refuses rather than committing
# credentials validated against a stale address.
_REAUTH_MAX_REVALIDATIONS = 3

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


def build_soc_step_schema(hass, defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    """Build the advanced-mode SOC value step, prefilled from any ev_* helpers.

    ``defaults`` (e.g. the live entry data) takes precedence: a value the user
    already stored must prefill the form so re-submitting it cannot silently
    replace it with a generic default.
    """
    cap_lo, cap_hi = SOC_INPUT_LIMITS["battery_capacity"]
    cor_lo, cor_hi = SOC_INPUT_LIMITS["soc_correction"]
    defaults = defaults or {}
    cap_default = normalize_soc_input(
        "battery_capacity",
        defaults.get(CONF_BATTERY_CAPACITY),
        _prefill_from_helper(
            hass, "input_number.ev_battery_capacity", "battery_capacity", DEFAULT_BATTERY_CAPACITY
        ),
    )
    cor_default = normalize_soc_input(
        "soc_correction",
        defaults.get(CONF_SOC_CORRECTION),
        _prefill_from_helper(
            hass, "input_number.ev_soc_correction", "soc_correction", DEFAULT_SOC_CORRECTION
        ),
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

    # urlparse silently DROPS ASCII control characters (\n, \r, \t) from the
    # host, so "a\nb.com" would normalize to "ab.com" — a different target than
    # the user typed. Reject them outright instead of connecting to a host the
    # user never entered.
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw_host):
        raise vol.Invalid("Host contains invalid control characters")

    # A bare IPv6 literal (2+ colons, no brackets, no scheme) confuses urlparse,
    # which reads the trailing group as a port. Bracket it so it parses as a host.
    if "://" not in raw_host and "[" not in raw_host and raw_host.count(":") >= 2:
        raw_host = f"[{raw_host}]"

    # urlparse raises ValueError on an unbalanced IPv6 bracket (e.g. "[::1",
    # "http://[::1"). Map it to vol.Invalid so setup/reconfigure surface
    # "invalid_input" and a stored bad host raises the repairable invalid-config
    # issue, instead of a generic "unknown" / ConfigEntryNotReady retry loop.
    try:
        parsed = urlparse(raw_host if "://" in raw_host else f"//{raw_host}")
    except ValueError as err:
        raise vol.Invalid(_INVALID_HOST_MSG) from err
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
        raise vol.Invalid(_INVALID_HOST_MSG)

    try:
        port = parsed.port
    except ValueError as err:
        raise vol.Invalid("Invalid port") from err

    if port is not None and not 1 <= port <= 65535:
        raise vol.Invalid("Invalid port")

    # Drop an explicit scheme-default port (http:80 / https:443): "host" and
    # "host:80" address the same endpoint and must collapse to one unique ID, or
    # the same charger can be added twice as two devices.
    if port == (80 if scheme == "http" else 443):
        port = None

    if not _host_is_valid(hostname):
        raise vol.Invalid(_INVALID_HOST_MSG)

    if hostname.endswith("."):
        hostname = hostname[:-1]

    if not is_ip_address(hostname):
        hostname = hostname.lower()

    is_ipv6 = ":" in hostname
    if is_ipv6:
        # Canonicalize the IPv6 literal so equivalent spellings (e.g. "::1" vs
        # "0:0:0:0:0:0:0:1") collapse to one identity instead of two devices.
        try:
            hostname = ipaddress.ip_address(hostname).compressed
        except ValueError:
            pass
        if not hostname.startswith("["):
            hostname = f"[{hostname}]"

    if port is not None:
        return f"{hostname}:{port}", scheme
    return hostname, scheme


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

    # aiohttp encodes Basic Auth as latin-1 at request time; credentials it
    # cannot encode would otherwise surface later as a confusing
    # "cannot connect" instead of an actionable invalid-credentials error.
    try:
        aiohttp.BasicAuth(username, password).encode()
    except UnicodeEncodeError as err:
        raise vol.Invalid(
            "Username and password must use Latin-1 compatible characters"
        ) from err

    return username, password


# Keys that identify a /main response as coming from an Eveus charger. Setup
# only needs to confirm "this really is an Eveus charger", so any one of these is
# enough — older firmware that omits some control fields still adds cleanly.
_EVEUS_SIGNATURE_KEYS: frozenset[str] = frozenset(
    {"state", "currentSet", "verFWMain", "verFWWifi", "curDesign", "evseType"}
)


def validate_device_response(
    result: Any,
    model: str,
) -> dict[str, Any]:
    """Confirm /main returned a recognizable Eveus payload (lenient at setup).

    The live poll keeps the strict field validation. Setup is deliberately
    lenient: older firmware reports fewer fields than current firmware (which
    adds, e.g., OCPP control fields), so requiring the full schema here blocks
    perfectly usable chargers. We only check that the response is an Eveus-shaped
    JSON object; the selected model is NOT validated against the reported
    curDesign (the charger enforces its own current limit internally).
    """
    if not isinstance(result, dict) or not (_EVEUS_SIGNATURE_KEYS & result.keys()):
        raise InvalidResponse("Response is not a recognizable Eveus payload")

    current_raw = result.get("currentSet")
    try:
        current_set = float(current_raw) if current_raw is not None else None
    except (TypeError, ValueError):
        current_set = None
    return {
        "current_set": current_set,
        "firmware": result.get("verFWMain", "Unknown"),
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
    if isinstance(raw_phases, float) and not raw_phases.is_integer():
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


def _safe_model_default(raw: Any) -> str:
    """Coerce stored model data to a valid option, falling back on MODEL_16A.

    A corrupt/foreign stored value would otherwise seed vol.In(MODELS) with a
    default outside its own allowed set on the reconfigure/repair form.
    """
    return raw if raw in MODELS else MODEL_16A


def build_user_data_schema(
    defaults: dict[str, Any] | None = None, *, include_soc_mode: bool = True
) -> vol.Schema:
    """Build config-flow schema with optional defaults.

    ``include_soc_mode`` is False for the reconfigure and repair flows: those
    edit connection details only and must NOT offer the SOC-mode chooser, because
    switching to Advanced there would commit without collecting the set-once
    battery values (the SOC-step follow-up lives only in the setup and options
    flows). SOC mode is changed through Configure (the options flow).
    """
    defaults = defaults or {}
    host_default = defaults.get(CONF_HOST)
    if host_default and defaults.get(CONF_SCHEME) == "https":
        host_default = f"https://{host_default}"
    schema: dict[Any, Any] = {
        vol.Required(CONF_HOST, default=host_default): str,
        vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME)): str,
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Required(
            CONF_MODEL,
            default=_safe_model_default(defaults.get(CONF_MODEL)),
        ): vol.In(MODELS),
        vol.Required(
            CONF_PHASES,
            default=_safe_phases_default(defaults.get(CONF_PHASES)),
        # Coerce first: the frontend (mobile app in particular) submits the
        # selected option as a string ("1"/"3"), which bare vol.In rejects
        # with "value must be one of [1, 3]" for every choice (issue #4).
        ): vol.All(vol.Coerce(int), vol.In(PHASE_OPTIONS)),
    }
    if include_soc_mode:
        schema[
            vol.Required(
                CONF_SOC_MODE,
                default=defaults.get(CONF_SOC_MODE, SOC_MODE_ADVANCED),
            )
        ] = _SOC_MODE_SELECTOR
    return vol.Schema(schema)


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
        # Same budget as the coordinator's regular poll (UPDATE_TIMEOUT): setup
        # is the one moment a struggling charger most needs patience, so it
        # must not be stricter than steady-state polling ever is.
        timeout = aiohttp.ClientTimeout(total=UPDATE_TIMEOUT)

        async with session.post(
            f"{normalized_data[CONF_SCHEME]}://{normalized_data[CONF_HOST]}/main",
            auth=aiohttp.BasicAuth(
                normalized_data[CONF_USERNAME],
                normalized_data[CONF_PASSWORD],
            ),
            timeout=timeout,
        ) as response:
            host = normalized_data[CONF_HOST]
            if response.status == 401:
                raise InvalidAuth("Invalid credentials")
            response.raise_for_status()

            # Read the raw body ourselves (instead of response.json) so a
            # misbehaving charger's reply can be logged before we try to decode
            # it. Older firmware that answers /main with an HTML login page or a
            # malformed body lands here, and the log makes the cause visible
            # rather than collapsing into a bare "Failed to connect".
            try:
                raw_body = await read_body_capped(response)
            except PayloadError as err:
                _LOGGER.debug(
                    "Eveus %s returned an oversized /main body (HTTP %s, %s)",
                    host, response.status, response.headers.get("Content-Type"),
                )
                raise InvalidResponse("Response body too large") from err

            try:
                result = json.loads(raw_body)
            except ValueError as err:
                # Debug-only, and only the already-capped first 200 bytes: enough
                # to tell an HTML login page from malformed JSON without dumping a
                # whole body. The /main body carries device telemetry, not the
                # Basic-Auth credentials, but it is still device-returned content,
                # so it stays behind the debug flag.
                _LOGGER.debug(
                    "Eveus %s did not return JSON from /main "
                    "(HTTP %s, Content-Type %s, first 200 bytes: %r)",
                    host, response.status,
                    response.headers.get("Content-Type"), raw_body[:200],
                )
                raise InvalidResponse("Response is not valid JSON") from err

            try:
                device_info = validate_device_response(result, normalized_data[CONF_MODEL])
            except InvalidResponse:
                _LOGGER.debug(
                    "Eveus %s returned JSON that is not an Eveus /main payload "
                    "(keys: %s)",
                    host,
                    sorted(result)[:20] if isinstance(result, dict) else type(result).__name__,
                )
                raise

            return {
                "title": f"Eveus Charger ({host})",
                "data": normalized_data,
                "device_info": device_info,
            }

    except aiohttp.ClientResponseError as err:
        if err.status == 401:
            raise InvalidAuth from err
        _LOGGER.debug(
            "Eveus %s returned HTTP %s for /main", normalized_data[CONF_HOST], err.status
        )
        raise CannotConnect(f"HTTP {err.status}") from err
    except (asyncio.TimeoutError, aiohttp.ClientError) as err:
        _LOGGER.debug(
            "Eveus %s is unreachable (%s)",
            normalized_data[CONF_HOST], type(err).__name__,
        )
        raise CannotConnect(f"Connection error: {type(err).__name__}") from err
    except (InvalidAuth, InvalidDevice, InvalidResponse, InvalidInput, CannotConnect):
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
            except InvalidResponse as err:
                errors["base"] = "invalid_response"
                _LOGGER.debug("Invalid response: %s", str(err))
            except AbortFlow:
                # `_abort_if_unique_id_configured()` raises AbortFlow, which is an
                # Exception subclass. Let it propagate so the duplicate charger
                # aborts with "already_configured" instead of a generic "unknown".
                raise
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        # Re-show with the submitted values as defaults so a validation error
        # (wrong password, unreachable host, ...) doesn't wipe the whole form —
        # only the password field is left blank, matching build_user_data_schema's
        # password widget (which never carries a default).
        schema = STEP_USER_DATA_SCHEMA if user_input is None else build_user_data_schema(
            user_input
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
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

    def _migrate_device_identifiers(self, entry, old_host: str, new_host: str) -> None:
        """Rewrite host-based device identifiers after an address change."""
        migrate_device_identifiers(self.hass, entry, old_host, new_host)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Allow users to reconfigure connection details."""
        entry = self._get_reconfigure_entry()
        errors = {}

        if user_input is not None:
            try:
                # The reconfigure form never collects CONF_SCHEME directly (it's
                # inferred from an "http://"/"https://" prefix typed into the host
                # field). If the user edits the host without retyping that
                # prefix, normalize_user_input's fallback would silently revert
                # to DEFAULT_SCHEME ("http") — downgrading an https entry to
                # plaintext. Seed the fallback with the entry's current scheme
                # instead, so an untouched prefix keeps whatever scheme was
                # already stored (still "http" for the common bare-IP case).
                reconfigure_input = {
                    CONF_SCHEME: entry.data.get(CONF_SCHEME, DEFAULT_SCHEME),
                    **user_input,
                }
                info = await validate_input(self.hass, reconfigure_input)
                entry_data = _merge_entry_data(entry.data, info["data"])
                # Reconfigure edits connection details only; never change SOC mode
                # here (its form omits the chooser). normalize_user_input defaults
                # an absent soc_mode to advanced, so re-assert the stored mode.
                entry_data[CONF_SOC_MODE] = get_soc_mode(entry)

                await self.async_set_unique_id(entry_data[CONF_HOST])
                if entry.unique_id != entry_data[CONF_HOST]:
                    self._abort_if_unique_id_configured()


                old_host = entry.data.get(CONF_HOST)
                new_host = entry_data[CONF_HOST]
                if old_host and old_host != new_host:
                    # The registry device is identified by host; migrate it so
                    # area assignments, custom names, and dashboard references
                    # follow the charger to its new address instead of leaving
                    # an orphaned device behind.
                    self._migrate_device_identifiers(entry, old_host, new_host)

                # This helper updates the entry and schedules exactly one reload.
                # The integration no longer registers a reloading update listener,
                # so there is no second reload on top of it (the HA 2026.6
                # deprecated double-reload).
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
            except InvalidResponse as err:
                errors["base"] = "invalid_response"
                _LOGGER.debug("Invalid reconfigure response: %s", str(err))
            except AbortFlow:
                # Duplicate-host abort must reach the user as "already_configured"
                # rather than being swallowed into a generic "unknown" error.
                raise
            except Exception:
                _LOGGER.exception("Unexpected reconfigure exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=build_user_data_schema(entry.data, include_soc_mode=False),
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
                # A concurrent reconfigure may change the connection details while
                # a validation is in flight, leaving credentials proven against an
                # OLD address. Snapshot host/scheme, validate, and re-validate
                # until the live details are unchanged across a full validation —
                # bounded, so even repeated mid-flight changes can't rebase
                # credentials onto an address that was never validated.
                info = None
                for _ in range(_REAUTH_MAX_REVALIDATIONS):
                    merged_data = dict(entry.data)
                    merged_data[CONF_USERNAME] = user_input[CONF_USERNAME]
                    merged_data[CONF_PASSWORD] = user_input[CONF_PASSWORD]
                    # The reauth form only exposes credentials, so a corrupt
                    # stored soc_mode would otherwise fail validation with no way
                    # to fix it. Normalize it to a valid mode before validating.
                    merged_data[CONF_SOC_MODE] = get_soc_mode(entry)
                    snapshot_host = merged_data.get(CONF_HOST)
                    snapshot_scheme = merged_data.get(CONF_SCHEME)

                    info = await validate_input(self.hass, merged_data)

                    live = dict(entry.data)
                    if (
                        live.get(CONF_HOST) == snapshot_host
                        and live.get(CONF_SCHEME) == snapshot_scheme
                    ):
                        break
                else:
                    # Host/scheme never settled; refuse rather than commit
                    # unvalidated credentials. cannot_connect lets the user retry.
                    raise CannotConnect

                # Rebase on LIVE entry data and replace only the credentials:
                # validate_input ran against a pre-await snapshot, so adopting
                # its full payload would roll back any options/reconfigure
                # change committed while the network validation was in flight.
                entry_data = dict(entry.data)
                entry_data[CONF_USERNAME] = merged_data[CONF_USERNAME]
                entry_data[CONF_PASSWORD] = merged_data[CONF_PASSWORD]

                await self.async_set_unique_id(entry_data[CONF_HOST])
                if entry.unique_id != entry_data[CONF_HOST]:
                    return self.async_abort(reason="wrong_device")

                # No device-identifier migration here: the reauth form only
                # exposes credentials, and any host mismatch aborted above as
                # wrong_device, so the host cannot change on this path.
                # One reload via the helper; no reloading update listener exists
                # to double it (the HA 2026.6 deprecated double-reload).
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
            except InvalidResponse as err:
                errors["base"] = "invalid_response"
                _LOGGER.debug("Invalid reauth response: %s", str(err))
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
    """Error to indicate invalid device response or capabilities.

    Retained as part of the setup error taxonomy: ``validate_input`` no longer
    raises it now that setup validation is lenient, but the flow steps and the
    repair flow still map it to the ``invalid_device`` message (covered by
    tests) so any future strict device check has a wired error path.
    """


class InvalidResponse(HomeAssistantError):
    """Reached the device, but it did not return a valid Eveus /main payload."""


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
            if user_input[CONF_SOC_MODE] == SOC_MODE_ADVANCED and (
                CONF_BATTERY_CAPACITY not in new_data
                or CONF_SOC_CORRECTION not in new_data
            ):
                # First switch to Advanced: collect the set-once SOC values,
                # same as the setup flow's soc step — otherwise the SOC inputs
                # would silently start from generic defaults.
                return await self.async_step_soc()
            return await self._apply(new_data)
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SOC_MODE, default=get_soc_mode(self._entry)
                ): _SOC_MODE_SELECTOR,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_soc(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Collect the set-once SOC values when first switching to Advanced."""
        if user_input is not None:
            # Rebase on the LIVE entry data, not a snapshot from when the form
            # opened: a reauth/reconfigure finishing while this form sat open
            # must not be rolled back to stale credentials/host on submit.
            new_data = dict(self._entry.data)
            new_data[CONF_SOC_MODE] = SOC_MODE_ADVANCED
            new_data[CONF_BATTERY_CAPACITY] = user_input[CONF_BATTERY_CAPACITY]
            new_data[CONF_SOC_CORRECTION] = user_input[CONF_SOC_CORRECTION]
            new_data.setdefault(CONF_INITIAL_SOC, DEFAULT_INITIAL_SOC)
            new_data.setdefault(CONF_TARGET_SOC, DEFAULT_TARGET_SOC)
            return await self._apply(new_data)
        return self.async_show_form(
            step_id="soc",
            data_schema=build_soc_step_schema(self.hass, defaults=self._entry.data),
        )

    async def _apply(self, new_data: dict[str, Any]) -> FlowResult:
        """Persist the updated entry data, reload the entry, and finish.

        The integration registers no reloading update listener, so the options
        flow reloads the entry itself for the new SOC mode to take effect.
        """
        self.hass.config_entries.async_update_entry(self._entry, data=new_data)
        await self.hass.config_entries.async_reload(self._entry.entry_id)
        return self.async_create_entry(title="", data={})


def migrate_device_identifiers(hass, entry, old_host: str, new_host: str) -> None:
    """Rewrite host-based device identifiers after an address change.

    Shared by the reconfigure flow and the invalid-config repair flow so a
    host change never orphans the device (its area, custom name, and
    dashboard references follow the charger to the new address).
    """
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        new_identifiers = set()
        changed = False
        for domain, ident in device.identifiers:
            if domain == DOMAIN and ident == old_host:
                new_identifiers.add((domain, new_host))
                changed = True
            elif domain == DOMAIN and ident.startswith(f"{old_host}_"):
                suffix = ident[len(old_host):]
                new_identifiers.add((domain, f"{new_host}{suffix}"))
                changed = True
            else:
                new_identifiers.add((domain, ident))
        if changed:
            registry.async_update_device(device.id, new_identifiers=new_identifiers)
