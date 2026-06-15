"""The Eveus integration."""
from __future__ import annotations

from dataclasses import dataclass
import logging
# NOT `import time`: this package has a `time.py` platform module, and the
# import system overwrites a package-global named `time` with that submodule
# the moment HA loads the time platform.
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
    BATTERY_LOW_THRESHOLD_VOLTS,
    BATTERY_VBAT_MAX_PLAUSIBLE_VOLTS,
    BATTERY_OK_THRESHOLD_VOLTS,
    BATTERY_LOW_DEBOUNCE_POLLS,
    CLOCK_DRIFT_THRESHOLD_SECONDS,
    CLOCK_DRIFT_TRIGGER_POLLS,
    CLOCK_DRIFT_CLEAR_POLLS,
    CLOCK_DRIFT_CLEAR_THRESHOLD_SECONDS,
    CLOCK_DRIFT_TZ_MATCH_TOLERANCE_SECONDS,
)
from .common_network import EveusUpdater
from .utils import (
    get_charger_wall_clock_seconds,
    get_local_utc_offset_seconds,
    get_local_wall_clock_seconds,
    get_device_suffix,
    get_next_device_number,
    get_safe_value,
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
    # Skip failed/unavailable polls: the coordinator notifies listeners on
    # failed refreshes too while retaining the previous payload (same guard as
    # the battery and clock-drift trackers).
    if not updater.available or not updater.last_update_success:
        return
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
    elif value == 0:
        # Only an explicit "off" clears the warning. A missing or out-of-domain
        # ocppEnabled (None) means the firmware dropped/garbled the field — leave
        # the prior issue state untouched rather than falsely dismissing it.
        ir.async_delete_issue(hass, DOMAIN, _ocpp_issue_id(entry))


def _battery_low_issue_id(entry: ConfigEntry) -> str:
    """Return the repair issue id for a depleted CR2032 coin cell."""
    return f"battery_low_{entry.entry_id}"


class _BatteryLowTracker:
    """Decide when to raise/clear the low RTC-battery warning.

    Applies hysteresis (fire below the low threshold, clear only above the
    higher OK threshold) and debounce (only fire after several consecutive low
    readings), so a battery hovering at the edge or a single glitchy ADC read
    can't make the warning flap or raise a false alarm.
    """

    def __init__(self) -> None:
        self._low_streak = 0
        self._active = False

    def evaluate(self, value: float | None) -> bool | None:
        """Return True to raise, False to clear, or None to leave unchanged.

        A missing/non-positive reading (offline or garbled `vBat`) is treated as
        "not low": it neither advances the debounce streak nor clears an active
        warning, mirroring how the OCPP warning ignores dropped fields.
        """
        if value is None or value <= 0 or value > BATTERY_VBAT_MAX_PLAUSIBLE_VOLTS:
            return None
        if value < BATTERY_LOW_THRESHOLD_VOLTS:
            self._low_streak += 1
            if self._low_streak >= BATTERY_LOW_DEBOUNCE_POLLS and not self._active:
                self._active = True
                return True
            return None
        # value >= low threshold: a healthy-enough reading restarts the debounce.
        self._low_streak = 0
        if value >= BATTERY_OK_THRESHOLD_VOLTS and self._active:
            self._active = False
            return False
        return None


def _update_battery_low_issue(
    hass: HomeAssistant, entry: ConfigEntry, updater, tracker: _BatteryLowTracker
) -> None:
    """Raise or clear the low coin-cell warning based on the latest poll.

    Non-fixable informational warning (the fix is a physical battery swap) that
    auto-clears once the replacement reads healthy.

    A failed or unavailable poll is skipped entirely: the coordinator notifies
    listeners on failed refreshes too while retaining the previous payload, so
    without this guard one genuine low reading followed by an outage would
    replay the stale sample into the debounce and raise a false warning.
    """
    if not updater.available or not updater.last_update_success:
        return
    value = get_safe_value(updater.data, "vBat", float) if updater.data else None
    decision = tracker.evaluate(value)
    if decision is True:
        ir.async_create_issue(
            hass,
            DOMAIN,
            _battery_low_issue_id(entry),
            is_fixable=False,
            is_persistent=False,
            issue_domain=DOMAIN,
            severity=ir.IssueSeverity.WARNING,
            translation_key="battery_low",
        )
    elif decision is False:
        ir.async_delete_issue(hass, DOMAIN, _battery_low_issue_id(entry))


def _clock_drift_issue_id(entry: ConfigEntry) -> str:
    """Return the repair issue id for a drifted charger clock."""
    return f"clock_drift_{entry.entry_id}"


class _ClockDriftTracker:
    """Decide when to raise/clear the charger clock-drift notice.

    Compares the charger's wall clock (``systemTime``, local-encoded epoch
    seconds) against Home Assistant's local wall clock — not UTC to UTC, which
    would cancel the ``timeZone`` select out and miss a wrong timezone or a
    DST mismatch entirely.
    Fires only after several consecutive polls more than the threshold away
    from Home Assistant's clock; clears only after consecutive in-sync polls.
    Missing/corrupt time fields neither advance nor reset either streak,
    mirroring the other notice trackers. This tracker only reports — fixing
    the clock stays a user action (Sync Time button).
    """

    def __init__(self) -> None:
        self._drift_streak = 0
        self._ok_streak = 0
        self._active = False
        # Classification of the most recent drifted reading, used to pick the
        # repair message: "timezone" when the drift sits at a non-zero whole
        # hour (wrong Time Zone select or DST mismatch — Sync Time won't fix
        # it), "sync" for any other offset (the RTC itself is off).
        self.kind = "sync"
        self.hours = 0
        # (kind, hours) the repair was last published with, owned by
        # _update_clock_drift_issue; None while no issue is active.
        self.published: tuple[str, int] | None = None
        self.still_drifted = False
        # Consecutive polls the live classification has differed from the
        # published one; re-keying waits for a stable streak so a drift
        # oscillating across a classification boundary can't rewrite the
        # issue on every poll.
        self.rekey_streak = 0

    def evaluate(self, data: dict[str, Any] | None) -> bool | None:
        """Return True to raise, False to clear, or None to leave unchanged."""
        charger_wall = get_charger_wall_clock_seconds(data)
        if charger_wall is None:
            # A successful poll that simply omits/corrupts the time fields tells
            # us nothing about the drift. Don't let it advance the re-key streak
            # on stale classification state, and don't leave `still_drifted` set
            # from an earlier sample (which would let two such polls re-publish a
            # stale message).
            self.still_drifted = False
            self.rekey_streak = 0
            return None
        signed_drift = charger_wall - get_local_wall_clock_seconds()
        whole_hours = round(signed_drift / 3600)
        # A fractional local offset (India +5:30, Nepal +5:45) is one the
        # charger's whole-hour Time Zone select can never represent: the best
        # achievable wall clocks sit at -residue or +(3600-residue) from HA
        # local. Drift matching either is the hardware limit, not a fixable
        # sync/timezone fault — it needs its own guidance.
        residue = get_local_utc_offset_seconds() % 3600
        if residue and any(
            abs(signed_drift - candidate) <= CLOCK_DRIFT_TZ_MATCH_TOLERANCE_SECONDS
            for candidate in (-residue, 3600 - residue)
        ):
            self.kind = "fractional"
            self.hours = 0
        elif (
            whole_hours != 0
            and abs(signed_drift - whole_hours * 3600)
            <= CLOCK_DRIFT_TZ_MATCH_TOLERANCE_SECONDS
        ):
            self.kind = "timezone"
            self.hours = abs(whole_hours)
        else:
            self.kind = "sync"
            self.hours = 0
        drift = abs(signed_drift)
        self.still_drifted = drift > CLOCK_DRIFT_THRESHOLD_SECONDS
        if drift > CLOCK_DRIFT_THRESHOLD_SECONDS:
            self._ok_streak = 0
            self._drift_streak += 1
            if self._drift_streak >= CLOCK_DRIFT_TRIGGER_POLLS and not self._active:
                self._active = True
                return True
            return None
        self._drift_streak = 0
        if self._active and drift > CLOCK_DRIFT_CLEAR_THRESHOLD_SECONDS:
            # Hysteresis band: under the trigger threshold but still minutes
            # wrong — not "recovered". Clearing requires consecutive polls
            # genuinely back in sync, so reset the streak.
            self._ok_streak = 0
            return None
        self._ok_streak += 1
        if self._active and self._ok_streak >= CLOCK_DRIFT_CLEAR_POLLS:
            self._active = False
            return False
        return None


def _update_clock_drift_issue(
    hass: HomeAssistant, entry: ConfigEntry, updater, tracker: _ClockDriftTracker
) -> None:
    """Raise or clear the clock-drift notice based on the latest poll.

    Non-fixable warning: the guided fix is the Time Zone select plus the Sync
    Time button — the integration deliberately never rewrites the charger
    clock on its own. Skips failed/unavailable polls so stale data is never
    replayed into the debounce (same guard as the battery notice).
    """
    if not updater.available or not updater.last_update_success:
        return
    decision = tracker.evaluate(updater.data if isinstance(updater.data, dict) else None)
    # Re-key an ACTIVE issue when the drift's classification changes (sync
    # <-> whole-hour timezone, or a different hour count) so the repair never
    # keeps recommending the wrong fix. Only while still drifted, and only
    # after the new classification has held for a full debounce streak — a
    # drift oscillating across a classification boundary must not rewrite the
    # issue on every poll.
    rekey = False
    if (
        decision is None
        and tracker.published is not None
        and tracker.still_drifted
        and (tracker.kind, tracker.hours) != tracker.published
    ):
        tracker.rekey_streak += 1
        rekey = tracker.rekey_streak >= CLOCK_DRIFT_TRIGGER_POLLS
    else:
        tracker.rekey_streak = 0

    if decision is True or rekey:
        translation_key = {
            "timezone": "clock_drift_timezone",
            "fractional": "clock_drift_fractional_timezone",
        }.get(tracker.kind, "clock_drift")
        ir.async_create_issue(
            hass,
            DOMAIN,
            _clock_drift_issue_id(entry),
            is_fixable=False,
            is_persistent=False,
            issue_domain=DOMAIN,
            severity=ir.IssueSeverity.WARNING,
            translation_key=translation_key,
            translation_placeholders=(
                {"hours": str(tracker.hours)} if tracker.kind == "timezone" else None
            ),
        )
        tracker.published = (tracker.kind, tracker.hours)
        tracker.rekey_streak = 0
    elif decision is False:
        ir.async_delete_issue(hass, DOMAIN, _clock_drift_issue_id(entry))
        tracker.published = None
        tracker.rekey_streak = 0


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


async def async_setup(_hass: HomeAssistant, _config: dict[str, Any]) -> bool:
    """Set up the Eveus component."""
    return True


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
                netloc = parts.netloc.rsplit("@", 1)[-1]
                if netloc and (
                    parts.username
                    or parts.password
                    or parts.path not in ("", "/")
                    or parts.query
                    or parts.fragment
                ):
                    sanitized = urlunparse((parts.scheme, netloc, "", "", "", ""))
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
        )
        from .ev_sensors import CachedSOCCalculator
        from .soc_limit import SocLimitController

        soc_calculator = CachedSOCCalculator()
        soc_limit = SocLimitController(hass, updater, soc_calculator)

        raw_phases = entry.data.get(CONF_PHASES, DEFAULT_PHASES)
        phases, phases_were_invalid = _resolve_phases(raw_phases)
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
            soc_calculator=soc_calculator,
            soc_limit=soc_limit,
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

        # Track the CR2032 coin cell (vBat) across polls and warn when it is
        # depleted, with debounce/hysteresis held in the tracker.
        battery_tracker = _BatteryLowTracker()

        @callback
        def _refresh_battery_issue() -> None:
            _update_battery_low_issue(hass, entry, updater, battery_tracker)

        entry.async_on_unload(updater.async_add_listener(_refresh_battery_issue))
        _refresh_battery_issue()

        # Warn when the charger clock has drifted from Home Assistant by more
        # than 10 minutes (schedules/tariffs would mistime). Report-only: the
        # notice walks the user to the Time Zone select + Sync Time button.
        clock_tracker = _ClockDriftTracker()

        @callback
        def _refresh_clock_drift_issue() -> None:
            _update_clock_drift_issue(hass, entry, updater, clock_tracker)

        entry.async_on_unload(updater.async_add_listener(_refresh_clock_drift_issue))
        _refresh_clock_drift_issue()

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
        entry.async_on_unload(updater.async_add_listener(safety_manager.process))
        safety_manager.process()

        entry.async_on_unload(updater.async_add_listener(soc_limit.process))
        soc_limit.process()

        # DataUpdateCoordinator constructed with config_entry already registers
        # async_shutdown on the entry unload lifecycle — no manual registration.
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        entry.async_on_unload(entry.add_update_listener(update_listener))

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

    except (ConfigEntryAuthFailed, ConfigEntryError, ConfigEntryNotReady):
        raise
    except Exception as ex:
        # Log the full traceback locally, but keep the host/URL out of the
        # user-facing setup error string, matching the redaction used on the
        # poll and config-flow error paths.
        _LOGGER.exception("Unexpected error setting up Eveus integration")
        raise ConfigEntryNotReady(f"Unexpected error: {type(ex).__name__}") from ex


async def update_listener(hass: HomeAssistant, entry: EveusConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete every per-entry repair issue when the entry is removed.

    Unload only clears the transient notices; persistent ones (invalid
    config, SOC dashboard migration, safety incidents) deliberately survive
    unload — but once the entry itself is gone they would sit in Repairs
    forever, referencing a charger that no longer exists.
    """
    from .safety import POLICIES, safety_issue_id

    ir.async_delete_issue(hass, DOMAIN, _invalid_config_issue_id(entry))
    ir.async_delete_issue(hass, DOMAIN, _ocpp_issue_id(entry))
    ir.async_delete_issue(hass, DOMAIN, _battery_low_issue_id(entry))
    ir.async_delete_issue(hass, DOMAIN, _clock_drift_issue_id(entry))
    ir.async_delete_issue(hass, DOMAIN, f"soc_dashboard_update_{entry.entry_id}")
    for policy in POLICIES:
        ir.async_delete_issue(hass, DOMAIN, safety_issue_id(entry, policy.key))


async def async_unload_entry(hass: HomeAssistant, entry: EveusConfigEntry) -> bool:
    """Unload a config entry."""
    # Delete the per-entry issues only after the platforms actually unloaded:
    # if unloading fails the entry stays loaded with its trackers latched, and a
    # prematurely deleted issue could not be recreated until full recovery.
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        ir.async_delete_issue(hass, DOMAIN, _ocpp_issue_id(entry))
        ir.async_delete_issue(hass, DOMAIN, _battery_low_issue_id(entry))
        ir.async_delete_issue(hass, DOMAIN, _clock_drift_issue_id(entry))
    return unloaded
