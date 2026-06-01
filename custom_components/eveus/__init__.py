"""The Eveus integration."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, CONF_HOST, CONF_USERNAME, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import entity_registry as er

from .const import (
    DOMAIN,
    MODEL_MAX_CURRENT,
    CONF_MODEL,
    CONF_SCHEME,
    DEFAULT_SCHEME,
    CONF_PHASES,
    DEFAULT_PHASES,
    PHASE_OPTIONS,
    CONF_SOC_MODE,
    SOC_MODE_BASIC,
    SOC_MODE_ADVANCED,
    get_soc_mode,
    CONF_INITIAL_SOC,
    CONF_TARGET_SOC,
    CONF_BATTERY_CAPACITY,
    CONF_SOC_CORRECTION,
    DEFAULT_INITIAL_SOC,
    DEFAULT_TARGET_SOC,
    DEFAULT_BATTERY_CAPACITY,
    DEFAULT_SOC_CORRECTION,
)
from .common_network import EveusUpdater
from .utils import (
    get_device_suffix,
    get_next_device_number,
    get_safe_value,
    is_device_number_taken,
    normalize_soc_input,
)

if TYPE_CHECKING:
    from .ev_sensors import CachedSOCCalculator

_LOGGER = logging.getLogger(__name__)

CONFIG_ENTRY_VERSION = 4

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.BUTTON,
    Platform.SELECT,
    Platform.TIME,
]


@dataclass
class EveusRuntimeData:
    """Runtime data for an Eveus config entry."""

    updater: EveusUpdater
    device_number: int
    title: str
    soc_calculator: CachedSOCCalculator
    phases: int = DEFAULT_PHASES


EveusConfigEntry = ConfigEntry[EveusRuntimeData]


def _invalid_config_issue_id(entry: ConfigEntry) -> str:
    """Return the repair issue id for an invalid config entry."""
    return f"invalid_config_{entry.entry_id}"


def _create_invalid_config_issue(
    hass: HomeAssistant,
    entry: ConfigEntry,
    reason: str,
) -> None:
    """Create a repair issue for stored setup data that cannot work."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _invalid_config_issue_id(entry),
        data={"entry_id": entry.entry_id, "reason": reason},
        is_fixable=True,
        is_persistent=True,
        issue_domain=DOMAIN,
        severity=ir.IssueSeverity.ERROR,
        translation_key="invalid_config",
    )


def _delete_invalid_config_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clear the invalid-config repair issue if it exists."""
    ir.async_delete_issue(hass, DOMAIN, _invalid_config_issue_id(entry))


def _legacy_helpers_present(hass: HomeAssistant) -> bool:
    """True when the old input_number SOC helpers are registered."""
    reg = er.async_get(hass)
    return bool(
        reg.async_get("input_number.ev_initial_soc")
        and reg.async_get("input_number.ev_battery_capacity")
    )


def _ocpp_issue_id(entry: ConfigEntry) -> str:
    """Return the repair issue id flagging that OCPP is enabled."""
    return f"ocpp_enabled_{entry.entry_id}"


def _update_ocpp_issue(hass: HomeAssistant, entry: ConfigEntry, updater) -> None:
    """Raise or clear the OCPP-enabled warning based on the latest poll.

    When OCPP is enabled the charger is driven by the OCPP backend / mobile
    app, which can override Charging Current, limits, and schedule, so those
    Home Assistant controls may not take effect. Surfaced as a non-fixable
    warning that auto-clears the moment OCPP is turned off — even if that
    happens from the mobile app rather than from HA.
    """
    value = get_safe_value(updater.data, "ocppEnabled", int) if updater.data else None
    if value == 1:
        ir.async_create_issue(
            hass,
            DOMAIN,
            _ocpp_issue_id(entry),
            is_fixable=False,
            is_persistent=False,
            issue_domain=DOMAIN,
            severity=ir.IssueSeverity.WARNING,
            translation_key="ocpp_enabled",
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, _ocpp_issue_id(entry))


async def async_setup(_hass: HomeAssistant, _config: dict[str, Any]) -> bool:
    """Set up the Eveus component."""
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entry data."""
    new_data = dict(entry.data)
    host = new_data.get(CONF_HOST)
    if isinstance(host, str) and host.startswith(("http://", "https://")):  # NOSONAR python:S5332 — local LAN device, HTTPS not available on charger firmware.
        from urllib.parse import urlparse, urlunparse
        from .config_flow import _split_host_and_scheme

        # Legacy entries may have stored a full URL with path (e.g. ".../main").
        # Strip the path/query/fragment before validation so migration recovers
        # cleanly instead of leaving a malformed host on the entry.
        sanitized = host
        try:
            parts = urlparse(host)
            if parts.path not in ("", "/") or parts.query or parts.fragment:
                sanitized = urlunparse((parts.scheme, parts.netloc, "", "", "", ""))
        except ValueError:
            sanitized = host

        try:
            new_data[CONF_HOST], new_data[CONF_SCHEME] = _split_host_and_scheme(sanitized)
        except vol.Invalid:
            _LOGGER.warning(
                "Could not normalize stored Eveus host for entry %s",
                getattr(entry, "entry_id", "<unknown>"),
            )

    if CONF_SCHEME not in new_data:
        new_data[CONF_SCHEME] = DEFAULT_SCHEME

    if CONF_PHASES not in new_data:
        new_data[CONF_PHASES] = DEFAULT_PHASES

    if CONF_SOC_MODE not in new_data:
        if _legacy_helpers_present(hass):
            new_data[CONF_SOC_MODE] = SOC_MODE_ADVANCED
            for entity_id, key, default in (
                ("input_number.ev_initial_soc", CONF_INITIAL_SOC, DEFAULT_INITIAL_SOC),
                ("input_number.ev_target_soc", CONF_TARGET_SOC, DEFAULT_TARGET_SOC),
                (
                    "input_number.ev_battery_capacity",
                    CONF_BATTERY_CAPACITY,
                    DEFAULT_BATTERY_CAPACITY,
                ),
                (
                    "input_number.ev_soc_correction",
                    CONF_SOC_CORRECTION,
                    DEFAULT_SOC_CORRECTION,
                ),
            ):
                st = hass.states.get(entity_id)
                # CONF_* values equal the SOC_INPUT_LIMITS keys.
                new_data[key] = normalize_soc_input(
                    key, st.state if st is not None else None, default
                )
        else:
            new_data[CONF_SOC_MODE] = SOC_MODE_BASIC

    update_kwargs: dict[str, Any] = {}
    if new_data != entry.data:
        update_kwargs["data"] = new_data

        if getattr(entry, "unique_id", None) == host:
            update_kwargs["unique_id"] = new_data[CONF_HOST]

        if isinstance(host, str) and isinstance(entry.title, str) and host in entry.title:
            update_kwargs["title"] = entry.title.replace(host, new_data[CONF_HOST])

    if getattr(entry, "version", 1) < CONFIG_ENTRY_VERSION:
        update_kwargs["version"] = CONFIG_ENTRY_VERSION

    if update_kwargs:
        hass.config_entries.async_update_entry(entry, **update_kwargs)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: EveusConfigEntry) -> bool:
    """Set up Eveus from a config entry."""
    try:
        host = entry.data.get(CONF_HOST)
        username = entry.data.get(CONF_USERNAME)
        password = entry.data.get(CONF_PASSWORD)
        model = entry.data.get(CONF_MODEL)
        scheme = entry.data.get(CONF_SCHEME, DEFAULT_SCHEME)

        if not host:
            _create_invalid_config_issue(hass, entry, "missing_host")
            raise ConfigEntryError("No host specified")
        if not isinstance(host, str):
            _create_invalid_config_issue(hass, entry, "invalid_host")
            raise ConfigEntryError("Host is not a string")

        from .config_flow import _split_host_and_scheme

        try:
            host, scheme = _split_host_and_scheme(host, scheme)
        except vol.Invalid as err:
            _create_invalid_config_issue(hass, entry, "invalid_host")
            raise ConfigEntryError(f"Invalid host: {err}") from err
        if not username:
            _create_invalid_config_issue(hass, entry, "missing_username")
            raise ConfigEntryError("No username specified")
        if not password:
            _create_invalid_config_issue(hass, entry, "missing_password")
            raise ConfigEntryError("No password specified")
        if model not in MODEL_MAX_CURRENT:
            _create_invalid_config_issue(hass, entry, "invalid_model")
            raise ConfigEntryError("Invalid model specified")
        if scheme not in ("http", "https"):
            _create_invalid_config_issue(hass, entry, "invalid_scheme")
            raise ConfigEntryError(f"Invalid scheme: {scheme!r}")

        _delete_invalid_config_issue(hass, entry)

        raw_device_number = entry.data.get("device_number")
        try:
            device_number = int(raw_device_number)
        except (TypeError, ValueError):
            device_number = None

        if (
            device_number is None
            or device_number < 1
            or is_device_number_taken(hass, device_number, entry.entry_id)
        ):
            device_number = get_next_device_number(hass, entry.entry_id)
            new_data = dict(entry.data)
            new_data["device_number"] = device_number
            hass.config_entries.async_update_entry(entry, data=new_data)
            _LOGGER.debug("Assigned Eveus device number %d", device_number)
        elif raw_device_number != device_number:
            new_data = dict(entry.data)
            new_data["device_number"] = device_number
            hass.config_entries.async_update_entry(entry, data=new_data)
            _LOGGER.debug("Normalized Eveus device number %d", device_number)

        # Purge the retired "Input Entities Status" sensor from the entity
        # registry so it does not linger as an unavailable/orphan entity after
        # upgrade. Its unique_id follows the base scheme keyed on device_number.
        reg = er.async_get(hass)
        status_unique_id = (
            f"eveus{get_device_suffix(device_number)}_input_entities_status"
        )
        stale = reg.async_get_entity_id("sensor", DOMAIN, status_unique_id)
        if stale:
            reg.async_remove(stale)

        if get_soc_mode(entry) == SOC_MODE_ADVANCED and _legacy_helpers_present(hass):
            ir.async_create_issue(
                hass,
                DOMAIN,
                f"soc_dashboard_update_{entry.entry_id}",
                is_fixable=False,
                is_persistent=True,
                issue_domain=DOMAIN,
                severity=ir.IssueSeverity.WARNING,
                translation_key="soc_dashboard_update",
            )

        updater = EveusUpdater(
            host=host,
            username=username,
            password=password,
            hass=hass,
            scheme=scheme,
            config_entry=entry,
        )
        from .ev_sensors import CachedSOCCalculator

        raw_phases = entry.data.get(CONF_PHASES, DEFAULT_PHASES)
        try:
            phases = int(raw_phases)
        except (TypeError, ValueError):
            phases = DEFAULT_PHASES
        if phases not in PHASE_OPTIONS:
            phases = DEFAULT_PHASES
        if raw_phases != phases:
            # Persist the normalized value so an invalid stored phase count is
            # not silently re-evaluated (and hiding phase 2/3 entities) on every
            # reload.
            normalized = dict(entry.data)
            normalized[CONF_PHASES] = phases
            hass.config_entries.async_update_entry(entry, data=normalized)
            _LOGGER.warning(
                "Eveus phase count %r was invalid; normalized to %d phase(s)",
                raw_phases,
                phases,
            )

        entry.runtime_data = EveusRuntimeData(
            updater=updater,
            device_number=device_number,
            title=entry.title,
            soc_calculator=CachedSOCCalculator(),
            phases=phases,
        )

        await updater.async_config_entry_first_refresh()

        # Keep the OCPP-enabled warning in sync with every poll, so it reflects
        # toggles made from the charger UI or mobile app, not just from HA.
        @callback
        def _refresh_ocpp_issue() -> None:
            _update_ocpp_issue(hass, entry, updater)

        entry.async_on_unload(updater.async_add_listener(_refresh_ocpp_issue))
        _refresh_ocpp_issue()

        # DataUpdateCoordinator constructed with config_entry already registers
        # async_shutdown on the entry unload lifecycle — no manual registration.
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        entry.async_on_unload(entry.add_update_listener(update_listener))

        return True

    except (ConfigEntryAuthFailed, ConfigEntryError, ConfigEntryNotReady):
        raise
    except Exception as ex:
        _LOGGER.exception("Unexpected error setting up Eveus integration: %s", ex)
        raise ConfigEntryNotReady(f"Unexpected error: {ex}")


async def update_listener(hass: HomeAssistant, entry: EveusConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: EveusConfigEntry) -> bool:
    """Unload a config entry."""
    ir.async_delete_issue(hass, DOMAIN, _ocpp_issue_id(entry))
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
