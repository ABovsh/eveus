"""Tests for Eveus safety Repairs notices."""
from __future__ import annotations

import asyncio
import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store

from custom_components.eveus import safety
from custom_components.eveus.const import (
    DOMAIN,
    ERROR_STATES,
    GROUND_CLEAR_POLLS,
    GROUND_CONTROL_CLEAR_POLLS,
    GROUND_CONTROL_TRIGGER_POLLS,
    GROUND_TRIGGER_POLLS,
    LEAKAGE_RECOVERED_MA,
    MAX_VALID_TEMPERATURE_C,
    TEMPERATURE_RECOVERY_POLLS,
    TEMPERATURE_TRIGGER_POLLS,
)
from custom_components.eveus.safety import (
    POLICIES,
    EveusSafetyManager,
    SafetyLifecycle,
    SafetyPolicyState,
    evaluate_policy_signals,
    safety_issue_id,
)

ROOT = Path(__file__).resolve().parents[1]


def _signals(key: str, payload: dict[str, object]) -> tuple[bool | None, bool | None]:
    policy = next(policy for policy in POLICIES if policy.key == key)
    return evaluate_policy_signals(policy, payload)


# ---------------------------------------------------------------------------
# In-memory issue-registry double.
#
# Real Home Assistant is installed in this environment, so the conftest no-HA
# stub never activates and the production code talks to the real issue
# registry. Production (``safety.ir``) and these tests both import the *same*
# ``homeassistant.helpers.issue_registry`` module object, so patching its
# functions (below, via an autouse fixture) routes both through this queryable
# in-memory double for the duration of each test. This mirrors how the existing
# battery/ocpp repair tests monkeypatch ``ir``, but adds lookup + ignored state.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeIssueEntry:
    dismissed_version: str | None = None


class _FakeIssueRegistry:
    def __init__(self) -> None:
        self.issues: dict[tuple[str, str], _FakeIssueEntry] = {}

    def async_get_issue(self, domain: str, issue_id: str) -> _FakeIssueEntry | None:
        return self.issues.get((domain, issue_id))


def _fake_async_get(hass: Any) -> _FakeIssueRegistry:
    registry = getattr(hass, "issue_registry", None)
    if not isinstance(registry, _FakeIssueRegistry):
        registry = _FakeIssueRegistry()
        hass.issue_registry = registry
    return registry


def _fake_async_create_issue(
    hass: Any, domain: str, issue_id: str, **kwargs: Any
) -> None:
    # setdefault so a redundant create cannot clobber a user's ignored state.
    _fake_async_get(hass).issues.setdefault((domain, issue_id), _FakeIssueEntry())


def _fake_async_delete_issue(hass: Any, domain: str, issue_id: str) -> None:
    _fake_async_get(hass).issues.pop((domain, issue_id), None)


def _fake_async_ignore_issue(
    hass: Any, domain: str, issue_id: str, ignore: bool
) -> None:
    registry = _fake_async_get(hass)
    registry.issues[(domain, issue_id)] = _FakeIssueEntry(
        dismissed_version="test-version" if ignore else None
    )


@pytest.fixture(autouse=True)
def _install_fake_issue_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ir, "async_get", _fake_async_get, raising=False)
    monkeypatch.setattr(
        ir, "async_create_issue", _fake_async_create_issue, raising=False
    )
    monkeypatch.setattr(
        ir, "async_delete_issue", _fake_async_delete_issue, raising=False
    )
    monkeypatch.setattr(
        ir, "async_ignore_issue", _fake_async_ignore_issue, raising=False
    )


def test_fake_issue_registry_models_ignored_state() -> None:
    hass = SimpleNamespace()

    ir.async_create_issue(
        hass,
        "eveus",
        "safety_test_entry",
        is_fixable=False,
        is_persistent=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="safety_test",
    )

    issue = ir.async_get(hass).async_get_issue("eveus", "safety_test_entry")
    assert issue is not None
    assert issue.dismissed_version is None

    ir.async_ignore_issue(hass, "eveus", "safety_test_entry", True)
    issue = ir.async_get(hass).async_get_issue("eveus", "safety_test_entry")
    assert issue is not None
    assert issue.dismissed_version is not None


def test_all_firmware_fault_codes_are_covered_exactly_once() -> None:
    owners: dict[int, list[str]] = {code: [] for code in ERROR_STATES if code != 0}
    for policy in POLICIES:
        for code in policy.fault_codes:
            owners[code].append(policy.key)

    assert set(owners) == set(range(1, 15))
    assert all(len(policy_keys) == 1 for policy_keys in owners.values())
    assert owners[2] == ["leakage_detected"]
    assert owners[4] == ["leakage_detected"]


def test_policy_lifecycles_match_safety_contract() -> None:
    by_key = {policy.key: policy for policy in POLICIES}
    assert by_key["ground_missing"].lifecycle is SafetyLifecycle.AUTO_CLEAR
    assert by_key["ground_control_disabled"].lifecycle is SafetyLifecycle.AUTO_CLEAR
    for key in set(by_key) - {"ground_missing", "ground_control_disabled"}:
        assert by_key[key].lifecycle is SafetyLifecycle.LATCHED


def test_safety_policy_is_frozen_and_slotted() -> None:
    policy = POLICIES[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.key = "mutated"
    assert not hasattr(policy, "__dict__")


def test_safety_policy_state_is_slotted() -> None:
    assert not hasattr(SafetyPolicyState(), "__dict__")


def test_safety_policy_state_defaults() -> None:
    state = SafetyPolicyState()
    assert state.trigger_streak == 0
    assert state.recovery_streak == 0
    assert state.recovered is False
    assert state.recovered_since_raised is False


def test_safety_store_uses_version_1() -> None:
    hass = SimpleNamespace(data={}, config=SimpleNamespace(config_dir="/tmp"))
    store = Store(hass, safety._SAFETY_STORE_VERSION, "test_key")
    assert store.version == 1


def test_safety_issue_ids_are_entry_scoped() -> None:
    one = SimpleNamespace(entry_id="one")
    two = SimpleNamespace(entry_id="two")
    assert safety_issue_id(one, "box_overheat") != safety_issue_id(two, "box_overheat")


def test_real_safe_payload_has_no_dangerous_trigger() -> None:
    payload = json.loads((ROOT / "tests/fixtures/real_main_response.json").read_text())
    triggered = {
        policy.key
        for policy in POLICIES
        if evaluate_policy_signals(policy, payload)[0] is True
    }
    assert triggered == {"ground_control_disabled"}


def test_missing_ground_triggers_even_when_ground_control_is_disabled() -> None:
    trigger, recovered = _signals(
        "ground_missing",
        {"state": 2, "subState": 0, "ground": 0, "groundCtrl": 0},
    )
    assert trigger is True
    assert recovered is False


def test_ground_control_disabled_is_a_separate_signal() -> None:
    assert _signals("ground_control_disabled", {"state": 2, "groundCtrl": 0}) == (
        True,
        False,
    )
    assert _signals("ground_control_disabled", {"state": 2, "groundCtrl": 1}) == (
        False,
        True,
    )


def test_missing_ground_and_disabled_ground_control_raise_independent_issues() -> None:
    hass, entry, updater, manager = _manager(
        {"state": 2, "subState": 0, "ground": 0, "groundCtrl": 0}
    )

    for _ in range(max(GROUND_TRIGGER_POLLS, GROUND_CONTROL_TRIGGER_POLLS)):
        manager.process()

    assert _issue(hass, entry, "ground_missing") is not None
    assert _issue(hass, entry, "ground_control_disabled") is not None

    updater.data = {"state": 2, "subState": 0, "ground": 0, "groundCtrl": 1}
    for _ in range(GROUND_CONTROL_CLEAR_POLLS):
        manager.process()

    assert _issue(hass, entry, "ground_missing") is not None
    assert _issue(hass, entry, "ground_control_disabled") is None

    hass, entry, updater, manager = _manager(
        {"state": 2, "subState": 0, "ground": 0, "groundCtrl": 0}
    )

    for _ in range(max(GROUND_TRIGGER_POLLS, GROUND_CONTROL_TRIGGER_POLLS)):
        manager.process()

    assert _issue(hass, entry, "ground_missing") is not None
    assert _issue(hass, entry, "ground_control_disabled") is not None

    updater.data = {"state": 2, "subState": 0, "ground": 1, "groundCtrl": 0}
    for _ in range(GROUND_CLEAR_POLLS):
        manager.process()

    assert _issue(hass, entry, "ground_missing") is None
    assert _issue(hass, entry, "ground_control_disabled") is not None


def test_ground_control_disabled_recovers_despite_unknown_state() -> None:
    # This policy has no firmware fault code: it is purely a groundCtrl raw
    # check. A malformed/missing `state` field must not block its recovery when
    # groundCtrl reads enabled, or an auto-clear notice could persist forever.
    assert _signals("ground_control_disabled", {"groundCtrl": 1}) == (False, True)
    assert (
        _signals(
            "ground_control_disabled",
            {"state": 7, "subState": 99, "groundCtrl": 1},
        )
        == (False, True)
    )
    # But an unknown groundCtrl itself must still leave recovery unknown.
    assert _signals("ground_control_disabled", {"state": 2})[1] is None


def test_temperature_warns_at_80_before_85_stop_and_uses_75_recovery_hysteresis() -> None:
    assert _signals("box_overheat", {"state": 2, "temperature1": 80}) == (True, False)
    assert _signals("plug_overheat", {"state": 2, "temperature2": 80}) == (True, False)
    assert _signals("box_overheat", {"state": 2, "temperature1": 79.9}) == (
        False,
        False,
    )
    assert _signals("box_overheat", {"state": 2, "temperature1": 75}) == (False, True)


def test_leakage_peak_never_triggers_without_live_leakage() -> None:
    assert _signals(
        "leakage_detected",
        {"state": 2, "leakValue": 0, "leakValueH": 90},
    ) == (False, True)


def test_temperature_boundary_at_max_valid_value_still_signals() -> None:
    # The physical-sanity upper bound is inclusive: a reading exactly at
    # MAX_VALID_TEMPERATURE_C is a real (if extreme) value, not an outlier to
    # discard as unknown.
    assert _signals(
        "box_overheat", {"state": 2, "temperature1": MAX_VALID_TEMPERATURE_C}
    ) == (True, False)


def test_leakage_recovery_boundary_is_strict() -> None:
    # Recovery requires strictly below the recovered threshold; a reading
    # exactly at LEAKAGE_RECOVERED_MA has not yet recovered.
    assert _signals(
        "leakage_detected", {"state": 2, "leakValue": LEAKAGE_RECOVERED_MA}
    ) == (False, False)


def test_bare_fault_code_policy_recovers_when_no_raw_recovery_defined() -> None:
    # relay_fault has no raw_recovered check: absence of its firmware fault
    # code alone must be enough to consider it recovered.
    assert _signals("relay_fault", {"state": 2}) == (False, True)


def test_fault_code_missing_or_zero_substate_stays_unknown_for_bare_policies() -> None:
    # A bare per-code policy has no raw signal to fall back on: if the
    # firmware fault code itself cannot be read (missing subState) or reports
    # "no cause" (subState 0), the result must stay unknown, never flip to a
    # definite "resolved".
    assert _signals("relay_fault", {"state": 7}) == (None, None)
    assert _signals("relay_fault", {"state": 7, "subState": 0}) == (None, None)


def test_corrupt_fault_code_does_not_override_definite_raw_trigger_false() -> None:
    # An unrecognized/future subState alongside a clearly-safe raw reading
    # must not be treated as "unknown" -- the trusted raw signal should still
    # give a definite answer.
    assert _signals(
        "box_overheat", {"state": 7, "subState": 99, "temperature1": 50}
    )[0] is False


def test_matching_firmware_fault_requires_the_specific_code() -> None:
    hass, entry, updater, manager = _manager(
        {"state": 7, "subState": 2, "temperature1": 90}
    )
    manager.process()
    # subState 2 is leakage_detected's code, not box_overheat's: the raw
    # temperature debounce still applies, it must not bypass on poll 1.
    assert _issue(hass, entry, "box_overheat") is None
    manager.process()
    assert _issue(hass, entry, "box_overheat") is not None


def test_unknown_or_corrupt_values_leave_signals_unknown() -> None:
    for payload in (
        {},
        {"state": 2, "temperature1": None},
        {"state": 2, "temperature1": True},
        {"state": 2, "temperature1": float("nan")},
        {"state": 2, "temperature1": 1e9},
        {"state": 7},
        {"state": 7, "subState": 99},
    ):
        trigger, recovered = _signals("box_overheat", payload)
        assert trigger is None
        assert recovered is None


def test_matching_firmware_fault_triggers_immediately() -> None:
    assert _signals("plug_overheat", {"state": 7, "subState": 6})[0] is True
    assert _signals("leakage_detected", {"state": 7, "subState": 4})[0] is True


def _manager(payload: dict[str, object] | None = None):
    hass = SimpleNamespace()
    entry = SimpleNamespace(entry_id="entry")
    updater = SimpleNamespace(
        data={} if payload is None else payload,
        available=True,
        last_update_success=True,
    )
    return hass, entry, updater, EveusSafetyManager(hass, entry, updater)


def _issue(hass, entry, key: str):
    return ir.async_get(hass).async_get_issue("eveus", safety_issue_id(entry, key))


def test_raw_temperature_requires_consecutive_valid_polls() -> None:
    hass, entry, updater, manager = _manager({"state": 2, "temperature1": 80})
    for _ in range(TEMPERATURE_TRIGGER_POLLS - 1):
        manager.process()
        assert _issue(hass, entry, "box_overheat") is None
    manager.process()
    assert _issue(hass, entry, "box_overheat") is not None


def test_healthy_reading_resets_partial_trigger_streak() -> None:
    hass, entry, updater, manager = _manager({"state": 2, "ground": 0})
    for _ in range(GROUND_TRIGGER_POLLS - 1):
        manager.process()
    updater.data = {"state": 2, "ground": 1}
    manager.process()
    updater.data = {"state": 2, "ground": 0}
    manager.process()
    assert _issue(hass, entry, "ground_missing") is None
    # A genuine reset restarts the debounce from scratch: GROUND_TRIGGER_POLLS
    # consecutive polls after the reset (not a head start from the partial run
    # before it) must be required before it triggers. One was already spent
    # above, so GROUND_TRIGGER_POLLS - 2 more must still show no issue.
    for _ in range(GROUND_TRIGGER_POLLS - 2):
        manager.process()
        assert _issue(hass, entry, "ground_missing") is None
    manager.process()
    assert _issue(hass, entry, "ground_missing") is not None


def test_unknown_reading_neither_advances_nor_resets_streak() -> None:
    hass, entry, updater, manager = _manager({"state": 2, "leakValue": 30})
    manager.process()
    updater.data = {"state": 2}
    manager.process()
    assert _issue(hass, entry, "leakage_detected") is None
    updater.data = {"state": 2, "leakValue": 30}
    manager.process()
    assert _issue(hass, entry, "leakage_detected") is not None


def test_failed_poll_does_not_replay_stale_data_into_debounce() -> None:
    hass, entry, updater, manager = _manager({"state": 2, "temperature1": 80})
    manager.process()
    updater.available = False
    updater.last_update_success = False
    manager.process()
    assert _issue(hass, entry, "box_overheat") is None
    updater.available = True
    updater.last_update_success = True
    manager.process()
    assert _issue(hass, entry, "box_overheat") is not None


def test_failed_poll_with_available_true_does_not_auto_clear_from_stale_data() -> None:
    # available=True but last_update_success=False (a failed poll while the
    # updater still reports itself "available") must skip exactly like a
    # fully-unavailable poll -- stale cached data must never auto-clear a fault.
    hass, entry, updater, manager = _manager(
        {"state": 7, "subState": 1, "ground": 0}
    )
    manager.process()
    assert _issue(hass, entry, "ground_missing") is not None
    updater.data = {"state": 2, "ground": 1}  # would-be recovery reading
    updater.available = True
    updater.last_update_success = False
    for _ in range(GROUND_CLEAR_POLLS):
        manager.process()
    assert _issue(hass, entry, "ground_missing") is not None


def test_firmware_fault_bypasses_raw_debounce() -> None:
    hass, entry, updater, manager = _manager({"state": 7, "subState": 5})
    manager.process()
    assert _issue(hass, entry, "box_overheat") is not None


def test_ground_missing_auto_clears_after_confirmed_recovery() -> None:
    hass, entry, updater, manager = _manager({"state": 7, "subState": 1, "ground": 0})
    manager.process()
    updater.data = {"state": 2, "ground": 1}
    manager.process()
    assert _issue(hass, entry, "ground_missing") is not None
    manager.process()
    assert _issue(hass, entry, "ground_missing") is None


def test_active_fault_followed_by_unknown_poll_does_not_falsely_auto_clear() -> None:
    # A firmware fault that is still active must not be marked "recovered" just
    # because the VERY NEXT poll happens to be corrupt/unknown -- an actual
    # recovery reading has never been observed.
    hass, entry, updater, manager = _manager({"state": 7, "subState": 1, "ground": 0})
    manager.process()
    assert _issue(hass, entry, "ground_missing") is not None
    updater.data = {}  # corrupt/unknown poll, immediately after an active fault
    manager.process()
    assert _issue(hass, entry, "ground_missing") is not None


def test_auto_cleared_issue_needs_full_fresh_debounce_on_recurrence() -> None:
    hass, entry, updater, manager = _manager({"state": 2, "ground": 0})
    for _ in range(GROUND_TRIGGER_POLLS):
        manager.process()
    assert _issue(hass, entry, "ground_missing") is not None
    updater.data = {"state": 2, "ground": 1}
    for _ in range(GROUND_CLEAR_POLLS):
        manager.process()
    assert _issue(hass, entry, "ground_missing") is None  # auto-cleared
    updater.data = {"state": 2, "ground": 0}  # fresh recurrence
    for _ in range(GROUND_TRIGGER_POLLS - 1):
        manager.process()
        assert _issue(hass, entry, "ground_missing") is None
    manager.process()
    assert _issue(hass, entry, "ground_missing") is not None


def test_recovered_latched_issue_remains_until_ignored() -> None:
    hass, entry, updater, manager = _manager({"state": 7, "subState": 5})
    manager.process()
    updater.data = {"state": 2, "temperature1": 70}
    for _ in range(TEMPERATURE_RECOVERY_POLLS):
        manager.process()
    assert _issue(hass, entry, "box_overheat") is not None
    ir.async_ignore_issue(hass, "eveus", safety_issue_id(entry, "box_overheat"), True)
    manager.process()
    assert _issue(hass, entry, "box_overheat") is None


def test_ignored_active_latched_issue_waits_for_recovery() -> None:
    hass, entry, updater, manager = _manager(
        {"state": 7, "subState": 2, "leakValue": 40}
    )
    manager.process()
    ir.async_ignore_issue(
        hass, "eveus", safety_issue_id(entry, "leakage_detected"), True
    )
    manager.process()
    assert _issue(hass, entry, "leakage_detected") is not None


def test_deleted_recovered_issue_can_alert_on_future_incident() -> None:
    hass, entry, updater, manager = _manager({"state": 7, "subState": 5})
    manager.process()
    ir.async_ignore_issue(hass, "eveus", safety_issue_id(entry, "box_overheat"), True)
    updater.data = {"state": 2, "temperature1": 70}
    for _ in range(TEMPERATURE_RECOVERY_POLLS):
        manager.process()
    assert _issue(hass, entry, "box_overheat") is None
    updater.data = {"state": 7, "subState": 5}
    manager.process()
    assert _issue(hass, entry, "box_overheat") is not None
    assert _issue(hass, entry, "box_overheat").dismissed_version is None


def test_recovered_then_acknowledged_issue_realerts_on_immediate_retrigger() -> None:
    # A latched issue that fully recovers BEFORE the user acknowledges (so it is
    # not deleted yet, by design it stays visible), is then acknowledged, and
    # re-triggers on the very next poll with no intervening recovered poll. The
    # new excursion must surface a fresh, un-dismissed notice rather than staying
    # hidden under the old acknowledgement.
    hass, entry, updater, manager = _manager({"state": 7, "subState": 5})
    manager.process()  # firmware overheat -> issue raised
    updater.data = {"state": 2, "temperature1": 70}
    for _ in range(TEMPERATURE_RECOVERY_POLLS):
        manager.process()  # confirmed recovery, latched + not ignored -> stays
    assert _issue(hass, entry, "box_overheat") is not None
    ir.async_ignore_issue(hass, "eveus", safety_issue_id(entry, "box_overheat"), True)

    updater.data = {"state": 7, "subState": 5}  # re-trigger, no cool poll first
    manager.process()
    issue = _issue(hass, entry, "box_overheat")
    assert issue is not None
    assert issue.dismissed_version is None


def test_recreated_issue_stops_being_sticky_after_immediate_retrigger() -> None:
    # Continuation of the scenario above: once the fresh excursion has
    # surfaced its own un-dismissed notice, the "recovered since raised"
    # memory must have been cleared for THIS incident. If the user dismisses
    # the fresh notice while it is still actively faulting (no new recovery),
    # it must stay dismissed -- not keep getting silently un-dismissed.
    hass, entry, updater, manager = _manager({"state": 7, "subState": 5})
    manager.process()
    updater.data = {"state": 2, "temperature1": 70}
    for _ in range(TEMPERATURE_RECOVERY_POLLS):
        manager.process()
    ir.async_ignore_issue(hass, "eveus", safety_issue_id(entry, "box_overheat"), True)
    updater.data = {"state": 7, "subState": 5}
    manager.process()  # fresh excursion recreates the notice
    issue = _issue(hass, entry, "box_overheat")
    assert issue is not None
    assert issue.dismissed_version is None

    ir.async_ignore_issue(hass, "eveus", safety_issue_id(entry, "box_overheat"), True)
    manager.process()  # still actively faulting, no recovery happened
    issue = _issue(hass, entry, "box_overheat")
    assert issue is not None
    assert issue.dismissed_version is not None


def test_recovered_then_acknowledged_issue_realerts_through_hysteresis_band() -> None:
    # After confirmed recovery + acknowledgement, a reading in the hysteresis
    # band (which resets the transient recovery streak) followed by a fresh
    # overheat must still re-alert -- the "recovered since raised" memory has to
    # survive the band, or the new excursion stays hidden under the old ack.
    hass, entry, updater, manager = _manager({"state": 7, "subState": 5})
    manager.process()
    updater.data = {"state": 2, "temperature1": 70}
    for _ in range(TEMPERATURE_RECOVERY_POLLS):
        manager.process()
    ir.async_ignore_issue(hass, "eveus", safety_issue_id(entry, "box_overheat"), True)

    updater.data = {"state": 2, "temperature1": 78}  # band: neither trigger nor recover
    manager.process()
    updater.data = {"state": 7, "subState": 5}  # fresh overheat
    manager.process()
    issue = _issue(hass, entry, "box_overheat")
    assert issue is not None
    assert issue.dismissed_version is None


def test_ongoing_acknowledged_issue_stays_quiet_without_recovery() -> None:
    # Guard against over-correction: an issue acknowledged while STILL active
    # (never recovered) must remain dismissed across continued triggers -- only a
    # genuine recover-then-retrigger may re-alert.
    hass, entry, updater, manager = _manager({"state": 7, "subState": 5})
    manager.process()
    ir.async_ignore_issue(hass, "eveus", safety_issue_id(entry, "box_overheat"), True)
    for _ in range(3):
        manager.process()  # still overheating, still acknowledged
    issue = _issue(hass, entry, "box_overheat")
    assert issue is not None
    assert issue.dismissed_version is not None


def test_unknown_poll_leaves_existing_issue_and_recovery_streak_untouched() -> None:
    # Raise via firmware fault, take one confirmed-recovery poll, then feed an
    # unknown poll: it must neither delete the issue nor reset the recovery
    # streak, so the next valid recovery poll still completes the clear.
    hass, entry, updater, manager = _manager({"state": 7, "subState": 1, "ground": 0})
    manager.process()
    updater.data = {"state": 2, "ground": 1}
    manager.process()  # recovery streak -> 1 of GROUND_CLEAR_POLLS (2)
    assert _issue(hass, entry, "ground_missing") is not None

    updater.data = {}  # unknown: trigger and recovered both None
    manager.process()
    assert _issue(hass, entry, "ground_missing") is not None  # not advanced/cleared

    updater.data = {"state": 2, "ground": 1}
    manager.process()  # streak preserved at 1 -> now 2 -> auto-clear
    assert _issue(hass, entry, "ground_missing") is None


def test_recurring_condition_resets_recovery_streak_before_clear() -> None:
    # A reading in the hysteresis band (75 < t < 80) neither triggers nor counts
    # as recovered; while the issue is open it must reset banked recovery so a
    # later clear needs the full streak again. Made observable by ignoring first
    # (latched + ignored deletes on confirmed recovery).
    hass, entry, updater, manager = _manager({"state": 7, "subState": 5})
    manager.process()  # firmware overheat -> issue raised
    ir.async_ignore_issue(hass, "eveus", safety_issue_id(entry, "box_overheat"), True)

    updater.data = {"state": 2, "temperature1": 70}
    manager.process()  # recovery streak -> 1
    updater.data = {"state": 2, "temperature1": 78}  # band: not recovered
    manager.process()  # recovery streak reset to 0
    assert _issue(hass, entry, "box_overheat") is not None

    updater.data = {"state": 2, "temperature1": 70}
    manager.process()  # streak 1
    manager.process()  # streak 2 -> would clear here had the reset not happened
    assert _issue(hass, entry, "box_overheat") is not None
    manager.process()  # streak 3 of TEMPERATURE_RECOVERY_POLLS -> clears
    assert _issue(hass, entry, "box_overheat") is None


@pytest.mark.parametrize(
    ("key", "payload", "polls", "expected_severity"),
    [
        ("ground_missing", {"state": 7, "subState": 1}, 1, ir.IssueSeverity.ERROR),
        (
            "ground_control_disabled",
            {"state": 2, "groundCtrl": 0},
            GROUND_CONTROL_TRIGGER_POLLS,
            ir.IssueSeverity.WARNING,
        ),
        ("leakage_detected", {"state": 7, "subState": 2}, 1, ir.IssueSeverity.ERROR),
        ("relay_fault", {"state": 7, "subState": 3}, 1, ir.IssueSeverity.ERROR),
        ("box_overheat", {"state": 7, "subState": 5}, 1, ir.IssueSeverity.ERROR),
        ("plug_overheat", {"state": 7, "subState": 6}, 1, ir.IssueSeverity.ERROR),
        ("pilot_fault", {"state": 7, "subState": 7}, 1, ir.IssueSeverity.ERROR),
        ("low_voltage", {"state": 7, "subState": 8}, 1, ir.IssueSeverity.ERROR),
        ("diode_fault", {"state": 7, "subState": 9}, 1, ir.IssueSeverity.ERROR),
        ("overcurrent", {"state": 7, "subState": 10}, 1, ir.IssueSeverity.ERROR),
        ("interface_timeout", {"state": 7, "subState": 11}, 1, ir.IssueSeverity.ERROR),
        ("software_failure", {"state": 7, "subState": 12}, 1, ir.IssueSeverity.ERROR),
        ("gfci_test_failure", {"state": 7, "subState": 13}, 1, ir.IssueSeverity.ERROR),
        ("high_voltage", {"state": 7, "subState": 14}, 1, ir.IssueSeverity.ERROR),
    ],
)
def test_issue_creation_metadata_and_no_active_poll_churn(
    monkeypatch, key, payload, polls, expected_severity
) -> None:
    original_create = ir.async_create_issue
    create_calls = []

    def capture_create(hass, domain, issue_id, **kwargs):
        create_calls.append((domain, issue_id, kwargs))
        original_create(hass, domain, issue_id, **kwargs)

    monkeypatch.setattr(ir, "async_create_issue", capture_create)
    hass, entry, updater, manager = _manager(payload)

    for _ in range(polls):
        manager.process()
    manager.process()

    assert len(create_calls) == 1
    domain, issue_id, kwargs = create_calls[0]
    assert domain == DOMAIN
    assert issue_id == safety_issue_id(entry, key)
    assert kwargs["is_fixable"] is False
    assert kwargs["is_persistent"] is True
    assert kwargs["severity"] is expected_severity
    assert kwargs["translation_key"] == f"safety_{key}"


def test_v05_persisted_recovery_lets_dismissed_issue_realert_after_reload() -> None:
    """A serious fault that recovered, was dismissed, then recurs across a reload
    must re-alert — the recovery memory survives manager recreation."""
    hass = SimpleNamespace()
    entry = SimpleNamespace(entry_id="entry")
    updater = SimpleNamespace(
        data={"state": 2, "temperature1": 80}, available=True, last_update_success=True
    )
    m1 = EveusSafetyManager(hass, entry, updater)
    for _ in range(TEMPERATURE_TRIGGER_POLLS):
        m1.process()
    assert _issue(hass, entry, "box_overheat") is not None

    # Recover (latched issue stays, but recovery is remembered).
    updater.data = {"state": 2, "temperature1": 70}
    for _ in range(TEMPERATURE_RECOVERY_POLLS):
        m1.process()
    assert m1._states["box_overheat"].recovered_since_raised is True
    snapshot = m1._persisted_snapshot()

    # User dismisses the lingering notice.
    ir.async_ignore_issue(hass, DOMAIN, safety_issue_id(entry, "box_overheat"), True)

    # Reload: a fresh manager restores the persisted recovery memory.
    m2 = EveusSafetyManager(hass, entry, updater)
    m2._apply_persisted(snapshot)

    # The fault recurs -> the stale dismissed notice is replaced by a fresh one.
    updater.data = {"state": 2, "temperature1": 80}
    for _ in range(TEMPERATURE_TRIGGER_POLLS):
        m2.process()
    issue = _issue(hass, entry, "box_overheat")
    assert issue is not None
    assert issue.dismissed_version is None


def test_v05_without_persisted_recovery_dismissed_issue_stays_hidden() -> None:
    """Control: with no recovery memory restored, a recurrence stays dismissed
    (the pre-fix behavior) — confirming the persistence is what re-alerts."""
    hass = SimpleNamespace()
    entry = SimpleNamespace(entry_id="entry")
    updater = SimpleNamespace(
        data={"state": 2, "temperature1": 80}, available=True, last_update_success=True
    )
    ir.async_create_issue(
        hass, DOMAIN, safety_issue_id(entry, "box_overheat"),
        is_fixable=False, is_persistent=True,
        severity=ir.IssueSeverity.ERROR, translation_key="safety_box_overheat",
    )
    ir.async_ignore_issue(hass, DOMAIN, safety_issue_id(entry, "box_overheat"), True)

    m = EveusSafetyManager(hass, entry, updater)  # no persisted memory restored
    for _ in range(TEMPERATURE_TRIGGER_POLLS):
        m.process()
    issue = _issue(hass, entry, "box_overheat")
    assert issue is not None
    assert issue.dismissed_version is not None  # stays hidden


def test_apply_persisted_defaults_missing_flag_to_false() -> None:
    hass, entry, updater, manager = _manager()
    manager._apply_persisted({"box_overheat": {}})
    assert manager._states["box_overheat"].recovered_since_raised is False


def test_async_load_restores_recovered_since_raised_from_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeStore:
        def __init__(self, hass_: Any, version: int, key: str) -> None:
            pass

        async def async_load(self) -> dict[str, Any]:
            return {"box_overheat": {"recovered_since_raised": True}}

    monkeypatch.setattr(safety, "Store", _FakeStore)
    hass, entry, updater, manager = _manager()

    asyncio.run(manager.async_load())

    assert manager._store is not None
    assert manager._states["box_overheat"].recovered_since_raised is True


def test_async_load_degrades_to_in_memory_only_on_store_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RaisingStore:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("storage unavailable")

    monkeypatch.setattr(safety, "Store", _RaisingStore)
    hass, entry, updater, manager = _manager()

    asyncio.run(manager.async_load())

    assert manager._store is None
