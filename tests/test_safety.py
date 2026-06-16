"""Tests for Eveus safety Repairs notices."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from homeassistant.helpers import issue_registry as ir

from custom_components.eveus.const import (
    DOMAIN,
    ERROR_STATES,
    GROUND_CLEAR_POLLS,
    GROUND_CONTROL_CLEAR_POLLS,
    GROUND_CONTROL_TRIGGER_POLLS,
    GROUND_TRIGGER_POLLS,
    TEMPERATURE_RECOVERY_POLLS,
    TEMPERATURE_TRIGGER_POLLS,
)
from custom_components.eveus.safety import (
    POLICIES,
    EveusSafetyManager,
    SafetyLifecycle,
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
