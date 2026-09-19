"""Non-fault repair notices: battery, clock drift, OCPP.

Battery and clock drift are software-side conditions (no firmware fault code
involved, unlike `safety.py`'s SafetyPolicy engine) whose raise/clear decision
is the same debounce-then-hysteresis shape: advance a trigger streak while the
condition holds, fire once it reaches a threshold; advance a recovery streak
while the condition has cleared, clear once THAT reaches a threshold.
`_evaluate_hysteresis` is that one shared kernel — `_BatteryLowTracker` and
`_ClockDriftTracker` in `__init__.py` used to each re-implement it by hand.

Clock drift additionally classifies (sync vs timezone vs fractional-timezone)
and re-keys an already-active issue when that classification changes — logic
with no counterpart in the shared kernel, layered on top of it instead.

OCPP has no debounce at all (the firmware flag doesn't dither) and its
"cleared" path has always been unconditional rather than gated on a prior
trigger, so it stays outside the shared kernel — see `update_ocpp_issue`.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import (
    BATTERY_LOW_DEBOUNCE_POLLS,
    BATTERY_LOW_THRESHOLD_VOLTS,
    BATTERY_OK_THRESHOLD_VOLTS,
    CLOCK_DRIFT_CLEAR_POLLS,
    CLOCK_DRIFT_CLEAR_THRESHOLD_SECONDS,
    CLOCK_DRIFT_THRESHOLD_SECONDS,
    CLOCK_DRIFT_TRIGGER_POLLS,
    CLOCK_DRIFT_TZ_MATCH_TOLERANCE_SECONDS,
    DOMAIN,
)
from .utils import get_local_utc_offset_seconds, get_local_wall_clock_seconds

_LOGGER = logging.getLogger(__name__)


def _evaluate_hysteresis(
    *,
    trigger_streak: int,
    recovery_streak: int,
    active: bool,
    is_bad: bool | None,
    recovered: bool | None,
    trigger_polls: int,
    recovery_polls: int,
) -> tuple[bool | None, int, int, bool]:
    """Return ``(decision, trigger_streak, recovery_streak, active)``.

    ``decision`` is True to raise, False to clear, or None to leave the issue
    as it is. ``is_bad``/``recovered`` are tri-state: None leaves the relevant
    streak untouched (a missing/corrupt reading must not advance or reset a
    debounce). The recovery streak advances on every ``recovered`` reading
    regardless of ``active`` (a tracker that has not fired yet still counts
    consecutive good readings, harmlessly), but only an active issue can
    actually be cleared by it.
    """
    if is_bad is True:
        recovery_streak = 0
        trigger_streak += 1
        if trigger_streak >= trigger_polls and not active:
            return True, trigger_streak, recovery_streak, True
        return None, trigger_streak, recovery_streak, active
    if is_bad is False:
        trigger_streak = 0

    if recovered is True:
        recovery_streak += 1
        if active and recovery_streak >= recovery_polls:
            return False, trigger_streak, recovery_streak, False
        return None, trigger_streak, recovery_streak, active
    if recovered is False:
        recovery_streak = 0
    return None, trigger_streak, recovery_streak, active


# ---------------------------------------------------------------------------
# Low RTC backup battery (CR2032 / vBat)
# ---------------------------------------------------------------------------


def battery_low_issue_id(entry: ConfigEntry) -> str:
    """Return the repair issue id for a depleted CR2032 coin cell."""
    return f"battery_low_{entry.entry_id}"


class BatteryLowTracker:
    """Decide when to raise/clear the low RTC-battery warning.

    Applies hysteresis (fire below the low threshold, clear only above the
    higher OK threshold) and debounce (only fire after several consecutive low
    readings), so a battery hovering at the edge or a single glitchy ADC read
    can't make the warning flap or raise a false alarm.
    """

    def __init__(self) -> None:
        self._low_streak = 0
        self._recovery_streak = 0
        self._active = False

    def evaluate(self, value: float | None) -> bool | None:
        """Return True to raise, False to clear, or None to leave unchanged.

        An unusable reading (offline, or a `vBat` the snapshot rejected as
        outside the coin cell's plausible window) is treated as "not low": it
        neither advances the debounce streak nor clears an active warning,
        mirroring how the OCPP warning ignores dropped fields.
        """
        is_bad = None if value is None else value < BATTERY_LOW_THRESHOLD_VOLTS
        recovered = None if value is None else value >= BATTERY_OK_THRESHOLD_VOLTS
        decision, self._low_streak, self._recovery_streak, self._active = (
            _evaluate_hysteresis(
                trigger_streak=self._low_streak,
                recovery_streak=self._recovery_streak,
                active=self._active,
                is_bad=is_bad,
                recovered=recovered,
                trigger_polls=BATTERY_LOW_DEBOUNCE_POLLS,
                # A single reading at/above the OK threshold clears immediately
                # — there never was a debounce on the recovery side.
                recovery_polls=1,
            )
        )
        return decision


def update_battery_low_issue(
    hass: HomeAssistant, entry: ConfigEntry, updater, tracker: BatteryLowTracker
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
    decision = tracker.evaluate(updater.snapshot.get("vBat"))
    if decision is True:
        ir.async_create_issue(
            hass,
            DOMAIN,
            battery_low_issue_id(entry),
            is_fixable=False,
            is_persistent=False,
            issue_domain=DOMAIN,
            severity=ir.IssueSeverity.WARNING,
            translation_key="battery_low",
        )
    elif decision is False:
        ir.async_delete_issue(hass, DOMAIN, battery_low_issue_id(entry))


# ---------------------------------------------------------------------------
# Charger clock drift
# ---------------------------------------------------------------------------


def clock_drift_issue_id(entry: ConfigEntry) -> str:
    """Return the repair issue id for a drifted charger clock."""
    return f"clock_drift_{entry.entry_id}"


class ClockDriftTracker:
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
        # update_clock_drift_issue; None while no issue is active.
        self.published: tuple[str, int] | None = None
        self.still_drifted = False
        # Consecutive polls the live classification has differed from the
        # published one; re-keying waits for a stable streak so a drift
        # oscillating across a classification boundary can't rewrite the
        # issue on every poll.
        self.rekey_streak = 0

    def evaluate(self, snapshot) -> bool | None:
        """Return True to raise, False to clear, or None to leave unchanged."""
        charger_wall = snapshot.charger_wall_clock_s
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
        is_bad = drift > CLOCK_DRIFT_THRESHOLD_SECONDS
        # Below the clear threshold is unconditionally "recovered enough to
        # count". Between the clear and trigger thresholds is a dead band:
        # before the issue is active there's nothing to protect (no test
        # matters yet, so it still counts, matching a poll that's simply not
        # bad); once active, that same reading must NOT count toward
        # clearing — hovering minutes-wrong is not recovery.
        recovered = drift <= CLOCK_DRIFT_CLEAR_THRESHOLD_SECONDS or not self._active
        decision, self._drift_streak, self._ok_streak, self._active = (
            _evaluate_hysteresis(
                trigger_streak=self._drift_streak,
                recovery_streak=self._ok_streak,
                active=self._active,
                is_bad=is_bad,
                recovered=recovered,
                trigger_polls=CLOCK_DRIFT_TRIGGER_POLLS,
                recovery_polls=CLOCK_DRIFT_CLEAR_POLLS,
            )
        )
        return decision


def update_clock_drift_issue(
    hass: HomeAssistant, entry: ConfigEntry, updater, tracker: ClockDriftTracker
) -> None:
    """Raise or clear the clock-drift notice based on the latest poll.

    Non-fixable warning: the guided fix is the Time Zone select plus the Sync
    Time button — the integration deliberately never rewrites the charger
    clock on its own. Skips failed/unavailable polls so stale data is never
    replayed into the debounce (same guard as the battery notice).
    """
    if not updater.available or not updater.last_update_success:
        return
    decision = tracker.evaluate(updater.snapshot)
    # Re-key an ACTIVE issue when the drift's classification changes (sync
    # <-> whole-hour timezone, or a different hour count) so the repair never
    # keeps recommending the wrong fix. Only while still drifted, and only
    # after the new classification has held for a full debounce streak — a
    # drift oscillating across a classification boundary must not rewrite the
    # issue on every poll.
    rekey = False  # pragma: no mutate - `rekey` is only ever consumed in a boolean `or` context below; None and False are equally falsy there
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
            clock_drift_issue_id(entry),
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
        ir.async_delete_issue(hass, DOMAIN, clock_drift_issue_id(entry))
        tracker.published = None
        tracker.rekey_streak = 0


# ---------------------------------------------------------------------------
# OCPP enabled
# ---------------------------------------------------------------------------


def ocpp_issue_id(entry: ConfigEntry) -> str:
    """Return the repair issue id flagging that OCPP is enabled."""
    return f"ocpp_enabled_{entry.entry_id}"


def update_ocpp_issue(hass: HomeAssistant, entry: ConfigEntry, updater) -> None:
    """Raise or clear the OCPP-enabled warning based on the latest poll.

    When OCPP is enabled the charger is driven by the OCPP backend / mobile
    app, which can override Charging Current, limits, and schedule, so those
    Home Assistant controls may not take effect. Surfaced as a non-fixable
    warning that auto-clears the moment OCPP is turned off — even if that
    happens from the mobile app rather than from HA.

    No tracker: the firmware's own `ocppEnabled` flag is authoritative and
    doesn't dither like a raw analog reading, so unlike battery/clock drift
    this reacts on every poll with no debounce — deliberately outside the
    shared hysteresis kernel above, whose "clear" path is gated on an active
    trigger; OCPP's "off" must clear unconditionally, matching its own prior
    stateless behaviour exactly.
    """
    # Skip failed/unavailable polls: the coordinator notifies listeners on
    # failed refreshes too while retaining the previous payload (same guard as
    # the battery and clock-drift trackers).
    if not updater.available or not updater.last_update_success:
        return
    value = updater.snapshot.get_int("ocppEnabled")
    if value == 1:
        ir.async_create_issue(
            hass,
            DOMAIN,
            ocpp_issue_id(entry),
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
        ir.async_delete_issue(hass, DOMAIN, ocpp_issue_id(entry))
