"""The Eveus integration."""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
import logging
import re
from pathlib import Path
# NOT `import time`: this package has a `time.py` platform module, and the
# import system overwrites a package-global named `time` with that submodule
# the moment HA loads the time platform.
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STARTED,
    Platform,
    CONF_HOST,
    CONF_USERNAME,
    CONF_PASSWORD,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .const import (
    DOMAIN,
    MODEL_MAX_CURRENT,
    OFFLINE_UPDATE_INTERVAL,
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
from .common_network import EveusUnreachable, EveusUpdater
from .issues import (
    BatteryLowTracker,
    ClockDriftTracker,
    battery_low_issue_id,
    clock_drift_issue_id,
    ocpp_issue_id,
    update_battery_low_issue,
    update_clock_drift_issue,
    update_ocpp_issue,
)
from .utils import (
    get_device_suffix,
    get_next_device_number,
    is_device_number_taken,
    normalize_soc_input,
)

if TYPE_CHECKING:
    from .ev_sensors import CachedSOCCalculator
    from .soc_limit import SocLimitController

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

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# The dashboard card ships with the integration: nothing to add by hand.
CARD_URL = f"/{DOMAIN}/eveus-card.js"
CARD_PATH = Path(__file__).parent / "frontend" / "eveus-card.js"
# unique_id is "eveus<device suffix>_<entity key>"; the card needs the key only.
_UNIQUE_ID_KEY = re.compile(r"^eveus\d*_(.+)$")


@dataclass
class EveusRuntimeData:
    """Runtime data for an Eveus config entry."""

    updater: EveusUpdater
    device_number: int
    title: str
    soc_calculator: CachedSOCCalculator
    soc_limit: SocLimitController
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


# SOC entities created only in Advanced mode, and per-phase sensors created only
# for a 3-phase entry. When the user reduces scope (Advanced -> Basic, or 3 -> 1
# phase) these are no longer built, so their registry rows must be pruned or they
# linger forever as orphaned "unavailable" entities.
_ADVANCED_ONLY_ENTITIES: tuple[tuple[str, str], ...] = (
    ("sensor", "soc_energy"),
    ("sensor", "soc_percent"),
    ("sensor", "time_to_target_soc"),
    ("sensor", "charging_finish_time"),
    ("sensor", "energy_to_target_soc"),
    ("sensor", "cost_to_target_soc"),
    ("number", "initial_soc"),
    ("number", "target_soc"),
    ("number", "battery_capacity"),
    ("number", "soc_correction"),
    ("switch", "limit_soc_enabled"),
)
# Entities retired from the integration entirely; always pruned so users
# don't keep an orphaned "unavailable" row after updating.
_REMOVED_ENTITIES: tuple[tuple[str, str], ...] = (
    ("sensor", "system_time"),  # replaced by time_drift
    ("switch", "adaptive_mode"),
    ("number", "minimum_voltage"),
    ("sensor", "adaptive_voltage_threshold"),
)
_THREE_PHASE_ONLY_ENTITIES: tuple[tuple[str, str], ...] = (
    ("sensor", "current_phase_2"),
    ("sensor", "current_phase_3"),
    ("sensor", "voltage_phase_2"),
    ("sensor", "voltage_phase_3"),
)


def _resolve_phases(raw_phases: Any) -> tuple[int, bool]:
    """Coerce a stored phase count, flagging values that were truly invalid.

    A string "1"/"3" from an older frontend is valid (just mistyped); anything
    unparseable or outside PHASE_OPTIONS is invalid and must not drive the
    destructive phase-entity prune — falling back to 1 phase and then pruning
    would permanently delete the phase 2/3 registry rows (areas, custom
    entity IDs) over a corrupt byte.
    """
    # bool is an int subclass: int(True) == 1 would otherwise count as a
    # valid one-phase config and drive the destructive prune.
    if isinstance(raw_phases, bool):
        return DEFAULT_PHASES, True
    try:
        phases = int(raw_phases)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_PHASES, True
    if phases not in PHASE_OPTIONS:
        return DEFAULT_PHASES, True
    return phases, False


def _prune_unused_entities(
    hass: HomeAssistant, device_number: int, soc_mode: str, phases: int
) -> None:
    """Remove registry rows for entities not built under the current config."""
    stale: list[tuple[str, str]] = list(_REMOVED_ENTITIES)
    if soc_mode != SOC_MODE_ADVANCED:
        stale.extend(_ADVANCED_ONLY_ENTITIES)
    if phases != 3:
        stale.extend(_THREE_PHASE_ONLY_ENTITIES)
    reg = er.async_get(hass)
    suffix = get_device_suffix(device_number)
    for platform, key in stale:
        unique_id = f"eveus{suffix}_{key}"
        entity_id = reg.async_get_entity_id(platform, DOMAIN, unique_id)
        if entity_id:
            reg.async_remove(entity_id)


async def async_setup(hass: HomeAssistant, _config: dict[str, Any]) -> bool:
    """Set up the Eveus component and register the dashboard card once."""

    async def _register_card(_event=None) -> None:
        # Lovelace resources exist only once the frontend has set up.
        try:
            await _async_register_card(hass)
        except Exception as err:  # noqa: BLE001 - the card must never block the charger
            _LOGGER.warning("Eveus card was not registered: %s", type(err).__name__)

    websocket_api.async_register_command(hass, _ws_card_entities)
    if hass.is_running:
        await _register_card()
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _register_card)
    return True


@websocket_api.websocket_command(
    {
        vol.Required("type"): "eveus/card_entities",
        vol.Optional("device_id"): str,
    }
)
@callback
def _ws_card_entities(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Map a charger's entity keys to entity_ids through the entity registry.

    Keyed by unique_id, so the card keeps working after a user renames an
    entity_id. Without a device_id the first charger is used.
    """
    registry = er.async_get(hass)
    wanted = msg.get("device_id")
    for entry in hass.config_entries.async_entries(DOMAIN):
        device_id = None
        entities: dict[str, str] = {}
        for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
            match = _UNIQUE_ID_KEY.match(ent.unique_id or "")
            if ent.platform != DOMAIN or match is None:
                continue
            if wanted is not None and ent.device_id != wanted:
                continue
            device_id = device_id or ent.device_id
            entities[match.group(1)] = ent.entity_id
        if entities:
            connection.send_result(msg["id"], {"device_id": device_id, "entities": entities})
            return
    connection.send_error(msg["id"], "not_found", "No Eveus charger found")


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the card and have every dashboard load it, with a cache-busting hash.

    Storage-mode dashboards get it as a Lovelace resource, exactly like a HACS
    card: resources load after the frontend is ready, while an extra module
    loads earlier and its element can be lost to the frontend's own registry
    setup ("Custom element doesn't exist"). YAML-mode resources cannot be
    written, so those fall back to the extra module.
    """
    if getattr(hass, "http", None) is None or "frontend" not in hass.config.components:
        return
    digest = await hass.async_add_executor_job(
        lambda: hashlib.sha256(CARD_PATH.read_bytes()).hexdigest()[:8]
    )
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(CARD_PATH), True)]
    )
    url = f"{CARD_URL}?v={digest}"
    lovelace = hass.data.get("lovelace")
    resources = getattr(lovelace, "resources", None)
    if getattr(lovelace, "resource_mode", None) != "storage" or not hasattr(
        resources, "async_create_item"
    ):
        add_extra_js_url(hass, url)
        return
    await resources.async_get_info()  # loads the collection on first use
    ours = [
        item for item in resources.async_items()
        if str(item.get("url", "")).split("?")[0] == CARD_URL
    ]
    if not ours:
        await resources.async_create_item({"res_type": "module", "url": url})
    elif ours[0]["url"] != url:
        await resources.async_update_item(
            ours[0]["id"], {"res_type": "module", "url": url}
        )


async def _async_unregister_card(hass: HomeAssistant, entry_id: str) -> None:
    """Remove the dashboard-card Lovelace resource once the last entry goes.

    Best effort: a storage or lovelace-data hiccup must not block entry
    removal, and other eveus entries must keep the resource they still use.
    Compared by entry_id, not list length: HA versions disagree on whether
    the entry being removed is still present in `async_entries()` at this
    point (2025.1 calls this before deleting it from the registry; current
    HA deletes it first), so only *other* entries may block removal.
    """
    try:
        others = [
            e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry_id
        ]
        if others:
            return
        lovelace = hass.data.get("lovelace")
        resources = getattr(lovelace, "resources", None)
        if not hasattr(resources, "async_items"):
            return
        for item in resources.async_items():
            if str(item.get("url", "")).split("?")[0] == CARD_URL:
                await resources.async_delete_item(item["id"])
    except (AttributeError, HomeAssistantError) as err:
        _LOGGER.debug(
            "Could not remove eveus dashboard card resource: %s",
            type(err).__name__,
        )


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entry data."""
    new_data = dict(entry.data)
    host = new_data.get(CONF_HOST)
    if isinstance(host, str) and host:
        from urllib.parse import urlparse, urlunparse
        from .config_flow import _split_host_and_scheme

        # Legacy entries may have stored a full URL with path (e.g. ".../main")
        # and/or embedded userinfo (http://user:pass@host). Strip the
        # path/query/fragment AND any credentials before validation so migration
        # recovers cleanly — and so leftover credentials can't survive in the
        # host or, via the title rewrite below, in the config-entry title.
        sanitized = host
        if host.lower().startswith(("http://", "https://")):  # NOSONAR python:S5332 — local LAN device, HTTPS not available on charger firmware.
            try:
                parts = urlparse(host)
            # netloc is "[user[:pass]@]host[:port]"; drop everything up to and
            # including the last '@' (preserves IPv6 brackets, which follow it).
                netloc = parts.netloc.rsplit("@", 1)[-1]  # pragma: no mutate - maxsplit only affects the discarded pieces; [-1] is identical for any maxsplit >= 1
                if netloc and (  # pragma: no mutate - netloc is only falsy for a hostless string, which _split_host_and_scheme rejects as unparseable either way (same warn-and-skip outcome)
                    parts.username
                    or parts.password
                    or parts.path not in ("", "/")  # pragma: no mutate - path is discarded by _split_host_and_scheme's re-parse (only "" and "/" are accepted, both collapse to the same hostname); whichever tuple member is mutated, the rebuilt sanitized string round-trips to an identical final host
                    or parts.query
                    or parts.fragment
                ):
                    sanitized = urlunparse((parts.scheme, netloc, "", "", "", ""))  # pragma: no mutate - the "params" slot is discarded: _split_host_and_scheme re-parses sanitized and never inspects .params
            except ValueError:
                sanitized = host

        # Canonicalize bare hosts too (case, trailing dot): a legacy entry that
        # keeps a non-canonical spelling in data/unique_id would let the same
        # charger be re-added under the canonical spelling as a duplicate.
        try:
            new_data[CONF_HOST], new_data[CONF_SCHEME] = _split_host_and_scheme(
                sanitized, new_data.get(CONF_SCHEME, DEFAULT_SCHEME)
            )
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

        if host is not None and getattr(entry, "unique_id", None) == host:
            new_unique_id = new_data[CONF_HOST]
            collision = any(
                other.entry_id != entry.entry_id and other.unique_id == new_unique_id
                for other in hass.config_entries.async_entries(DOMAIN)
            )
            if collision:
                # Two legacy entries differ only in address spelling; rewriting
                # would give them the same identity. Keep the old unique_id and
                # let the user resolve the duplicate explicitly.
                _LOGGER.warning(
                    "Skipping unique_id canonicalization for entry %s: "
                    "another entry already uses the canonical id",
                    entry.entry_id,
                )
            else:
                update_kwargs["unique_id"] = new_unique_id
                if new_data[CONF_HOST] != host:
                    # The stored address spelling was canonicalized (URL/path/
                    # credentials stripped, case/trailing-dot normalized). Carry
                    # the device's area, custom name, and dashboard references to
                    # the new host identifier so the device isn't orphaned and
                    # re-created from scratch on the next load.
                    from .config_flow import migrate_device_identifiers

                    migrate_device_identifiers(hass, entry, host, new_data[CONF_HOST])

        if isinstance(host, str) and isinstance(entry.title, str) and host in entry.title:
            update_kwargs["title"] = entry.title.replace(host, new_data[CONF_HOST])

    if getattr(entry, "version", 1) < CONFIG_ENTRY_VERSION:  # pragma: no mutate - default (1 vs 2) only matters for a version-less entry, and CONFIG_ENTRY_VERSION == 4 makes both "< 4" identically True
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

        from .config_flow import validate_credentials

        try:
            # Re-apply the config-flow credential rules to stored data so a
            # hand-edited or pre-validation entry (over-long, or ':' in the
            # username, which breaks Basic Auth) surfaces a repair instead of
            # silently failing every poll.
            username, password = validate_credentials(username, password)
        except vol.Invalid as err:
            _create_invalid_config_issue(hass, entry, "invalid_credentials")
            raise ConfigEntryError(f"Invalid credentials: {type(err).__name__}") from err
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
        except (TypeError, ValueError, OverflowError):
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

        soc_dashboard_issue_id = f"soc_dashboard_update_{entry.entry_id}"
        if get_soc_mode(entry) == SOC_MODE_ADVANCED and _legacy_helpers_present(hass):
            ir.async_create_issue(
                hass,
                DOMAIN,
                soc_dashboard_issue_id,
                is_fixable=False,
                is_persistent=True,
                issue_domain=DOMAIN,
                severity=ir.IssueSeverity.WARNING,
                translation_key="soc_dashboard_update",
            )
        else:
            # Clear the (persistent) notice once the legacy helpers are gone or the
            # entry leaves Advanced mode — otherwise the warning lingers forever.
            ir.async_delete_issue(hass, DOMAIN, soc_dashboard_issue_id)

        updater = EveusUpdater(
            host=host,
            username=username,
            password=password,
            hass=hass,
            scheme=scheme,
            config_entry=entry,
            device_number=device_number,
            model=model,
        )
        from .ev_sensors import CachedSOCCalculator
        from .soc_limit import SocLimitController

        soc_calculator = CachedSOCCalculator()
        # Seed from stored config data so a disabled SOC-input number entity
        # (whose async_added_to_hass never runs) can't blank SOC Percent, the
        # ETA sensors, or the SOC limit. The entity still overrides this the
        # moment it is added, restoring any newer/restored value.
        if get_soc_mode(entry) == SOC_MODE_ADVANCED:
            soc_calculator.set_value(
                "initial_soc",
                normalize_soc_input(
                    "initial_soc",
                    entry.data.get(CONF_INITIAL_SOC),
                    DEFAULT_INITIAL_SOC,
                ),
            )
            soc_calculator.set_value(
                "target_soc",
                normalize_soc_input(
                    "target_soc",
                    entry.data.get(CONF_TARGET_SOC),
                    DEFAULT_TARGET_SOC,
                ),
            )
            soc_calculator.set_value(
                "battery_capacity",
                normalize_soc_input(
                    "battery_capacity",
                    entry.data.get(CONF_BATTERY_CAPACITY),
                    DEFAULT_BATTERY_CAPACITY,
                ),
            )
            soc_calculator.set_value(
                "soc_correction",
                normalize_soc_input(
                    "soc_correction",
                    entry.data.get(CONF_SOC_CORRECTION),
                    DEFAULT_SOC_CORRECTION,
                ),
            )
        soc_limit = SocLimitController(hass, updater, soc_calculator)

        raw_phases = entry.data.get(CONF_PHASES, DEFAULT_PHASES)
        phases, phases_were_invalid = _resolve_phases(raw_phases)
        if phases_were_invalid:
            # Do NOT persist the fallback value: writing it into entry.data would
            # make raw_phases valid on the very next reload, losing the
            # phases_were_invalid signal that protects the phase 2/3 registry
            # rows from _prune_unused_entities below (see its 3-phase fallback).
            _LOGGER.warning(
                "Eveus phase count %r was invalid; using %d phase(s) for this session",
                raw_phases,
                phases,
            )

        # First refresh BEFORE publishing runtime_data: if it raises (auth,
        # transient, or payload failure) setup fails and the entry must carry no
        # runtime objects — otherwise diagnostics would report a failed setup as
        # ready and stale listeners could survive.
        # A charger nobody can reach is not a broken entry -- switching it off
        # between sessions is how these are used. ConfigEntryNotReady would
        # hand recovery to Home Assistant's setup backoff, which caps at
        # SETUP_RETRY_MAX_WAIT (10 minutes), so a charger powered back on can
        # stay missing for that long; the coordinator already owns this case
        # with a flat 60 s offline cycle and it never gets to run. So finish
        # setup: the entities exist and read unavailable, exactly as they do
        # for an outage mid-session, and the first poll that lands fills them.
        # Every other failure still fails setup -- bad credentials, or a reply
        # this firmware cannot produce, are not fixed by polling again.
        try:
            await updater.async_config_entry_first_refresh()
        except ConfigEntryNotReady as err:
            if not isinstance(err.__cause__, EveusUnreachable):
                raise
            _LOGGER.warning(
                "Eveus charger did not answer during setup; its entities start "
                "unavailable and it is polled every %d s until it returns",
                OFFLINE_UPDATE_INTERVAL,
            )
        # Firmware 1.x omits verFWMain from /main entirely (GitHub issue #11);
        # resolve a fallback from /init once, right after the first successful
        # poll, so device_info never has to show "Unknown" for those chargers.
        # Skipped when that poll did not happen: the probe is once-ever, and
        # spending it on a charger that is switched off would leave a fw-1.x
        # device showing "Unknown" for the whole session. device_info fills
        # sw_version in as soon as a real firmware string lands.
        # getattr-guarded: test doubles for EveusUpdater used elsewhere don't
        # all implement this, and it is not essential to setup succeeding.
        fetch_init_firmware = getattr(updater, "async_maybe_fetch_init_firmware", None)
        if callable(fetch_init_firmware) and updater.last_update_success:
            await fetch_init_firmware()

        entry.runtime_data = EveusRuntimeData(
            updater=updater,
            device_number=device_number,
            title=entry.title,
            soc_calculator=soc_calculator,
            soc_limit=soc_limit,
            phases=phases,
        )

        # From here, any failure must clear runtime_data so a half-built entry is
        # never left behind (diagnostics stays in the not-ready branch).
        try:
            return await _finish_setup(
                hass,
                entry,
                updater,
                soc_limit,
                device_number,
                phases,
                phases_were_invalid,
            )
        except asyncio.CancelledError:
            entry.runtime_data = None
            raise
        except Exception:
            entry.runtime_data = None
            raise

    except (ConfigEntryAuthFailed, ConfigEntryError, ConfigEntryNotReady):
        raise
    except Exception as ex:
        # Name only the exception class: its text or traceback can carry the
        # host/URL or response content, matching the redaction used on the
        # poll and config-flow error paths.
        _LOGGER.error("Unexpected error setting up Eveus integration: %s", type(ex).__name__)
        raise ConfigEntryNotReady(f"Unexpected error: {type(ex).__name__}") from ex


async def _finish_setup(
    hass: HomeAssistant,
    entry: "EveusConfigEntry",
    updater: "EveusUpdater",
    soc_limit: "SocLimitController",
    device_number: int,
    phases: int,
    phases_were_invalid: bool,
) -> bool:
    """Wire listeners, forward platforms, and prune, after runtime_data is set."""
    def follow_polls(process: Callable[[], None]) -> None:
        """Evaluate now and after every poll, until the entry unloads."""
        entry.async_on_unload(updater.async_add_listener(process))
        process()

    # Keep the OCPP-enabled warning in sync with every poll, so it reflects
    # toggles made from the charger UI or mobile app, not just from HA.
    follow_polls(lambda: update_ocpp_issue(hass, entry, updater))

    # Track the CR2032 coin cell (vBat) across polls and warn when it is
    # depleted, with debounce/hysteresis held in the tracker.
    battery_tracker = BatteryLowTracker()
    follow_polls(lambda: update_battery_low_issue(hass, entry, updater, battery_tracker))

    # Warn when the charger clock has drifted from Home Assistant by more
    # than 10 minutes (schedules/tariffs would mistime). Report-only: the
    # notice walks the user to the Time Zone select + Sync Time button.
    clock_tracker = ClockDriftTracker()
    follow_polls(lambda: update_clock_drift_issue(hass, entry, updater, clock_tracker))

    # Surface dangerous charger conditions (missing ground, leakage,
    # overheat, and firmware safety faults) as Home Assistant Repairs
    # notices. The manager owns its own debounce/hysteresis/latching. Its
    # listener is removed on unload (below), and its in-memory streaks reset
    # on reload — but the safety issues themselves are persistent and are
    # deliberately never deleted on unload, so incidents and ignored state
    # survive reloads, restarts, and temporary charger outages; the manager
    # reconciles recovery against the surviving issue after the next poll.
    from .safety import EveusSafetyManager

    safety_manager = EveusSafetyManager(hass, entry, updater)
    # Restore persisted recovery memory before the first reconciliation so a
    # dismissed-but-recovered safety issue can re-alert on a fresh fault.
    await safety_manager.async_load()
    follow_polls(safety_manager.process)

    # Cancel any in-flight SOC Stop on unload so it can't POST after teardown
    # (its task is created via hass.async_create_task, which HA does not bind to
    # this entry's lifecycle).
    entry.async_on_unload(soc_limit.async_shutdown)
    follow_polls(soc_limit.process)

    # DataUpdateCoordinator constructed with config_entry already registers
    # async_shutdown on the entry unload lifecycle — no manual registration.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # No reloading update listener is registered: the reconfigure/reauth flows
    # reload via async_update_reload_and_abort, the repair flow reloads
    # explicitly, and the options flow reloads itself. Registering a listener
    # that also reloads would double the unload/setup cycle on every entry
    # update (deprecated in HA 2026.6).

    # Drop registry rows for SOC/phase entities that this config no longer
    # builds (Advanced -> Basic, or 3 -> 1 phase). Deferred until the entry
    # is fully committed (first refresh + platforms up) so a transient
    # setup failure that HA will retry cannot permanently delete entities —
    # along with their area, disabled state, and custom entity_id — for a
    # reduced scope that never actually finished loading.
    _prune_unused_entities(
        hass,
        device_number,
        get_soc_mode(entry),
        # An invalid stored phase count fell back to 1; don't let that
        # fallback prune the 3-phase registry rows.
        3 if phases_were_invalid else phases,
    )

    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete every per-entry repair issue when the entry is removed.

    Unload only clears the transient notices; persistent ones (invalid
    config, SOC dashboard migration, safety incidents) deliberately survive
    unload — but once the entry itself is gone they would sit in Repairs
    forever, referencing a charger that no longer exists.
    """
    from .safety import (
        POLICIES,
        _SAFETY_STORE_VERSION,
        safety_issue_id,
        safety_store_key,
    )
    from homeassistant.helpers.storage import Store

    ir.async_delete_issue(hass, DOMAIN, _invalid_config_issue_id(entry))
    ir.async_delete_issue(hass, DOMAIN, ocpp_issue_id(entry))
    ir.async_delete_issue(hass, DOMAIN, battery_low_issue_id(entry))
    ir.async_delete_issue(hass, DOMAIN, clock_drift_issue_id(entry))
    ir.async_delete_issue(hass, DOMAIN, f"soc_dashboard_update_{entry.entry_id}")
    for policy in POLICIES:
        ir.async_delete_issue(hass, DOMAIN, safety_issue_id(entry, policy.key))

    # Remove the persisted safety recovery-memory store for this entry. Best
    # effort: a storage hiccup must not block entry removal.
    try:
        await Store(hass, _SAFETY_STORE_VERSION, safety_store_key(entry)).async_remove()
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Could not remove safety store for removed entry")

    await _async_unregister_card(hass, entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: EveusConfigEntry) -> bool:
    """Unload a config entry."""
    # Delete the per-entry issues only after the platforms actually unloaded:
    # if unloading fails the entry stays loaded with its trackers latched, and a
    # prematurely deleted issue could not be recreated until full recovery.
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        ir.async_delete_issue(hass, DOMAIN, ocpp_issue_id(entry))
        ir.async_delete_issue(hass, DOMAIN, battery_low_issue_id(entry))
        ir.async_delete_issue(hass, DOMAIN, clock_drift_issue_id(entry))
    return unloaded
