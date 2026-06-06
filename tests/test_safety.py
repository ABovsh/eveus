"""Tests for Eveus safety Repairs notices."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from homeassistant.helpers import issue_registry as ir

from custom_components.eveus.const import ERROR_STATES
from custom_components.eveus.safety import (
    POLICIES,
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


def test_temperature_uses_85_trigger_and_75_recovery_hysteresis() -> None:
    assert _signals("box_overheat", {"state": 2, "temperature1": 85}) == (True, False)
    assert _signals("box_overheat", {"state": 2, "temperature1": 80}) == (False, False)
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
