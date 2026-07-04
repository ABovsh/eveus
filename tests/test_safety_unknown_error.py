"""Catch-all safety policy for an Error state with no recognizable fault code."""
from __future__ import annotations

from custom_components.eveus.safety import POLICIES, evaluate_policy_signals


def _policy():
    matches = [p for p in POLICIES if p.key == "unknown_error"]
    assert len(matches) == 1
    return matches[0]


def _signals(data):
    return evaluate_policy_signals(_policy(), data)


def test_error_with_zero_substate_triggers() -> None:
    trigger, recovered = _signals({"state": 7, "subState": 0})
    assert trigger is True
    assert recovered is False


def test_error_with_unmapped_future_code_triggers() -> None:
    trigger, recovered = _signals({"state": 7, "subState": 99})
    assert trigger is True
    assert recovered is False


def test_error_with_known_code_does_not_trigger() -> None:
    """A recognized code belongs to its own per-code policy."""
    trigger, recovered = _signals({"state": 7, "subState": 3})
    assert trigger is False
    assert recovered is True


def test_normal_state_does_not_trigger() -> None:
    trigger, recovered = _signals({"state": 4, "subState": 0})
    assert trigger is False
    assert recovered is True


def test_invalid_state_is_unknown_and_moves_no_counter() -> None:
    trigger, recovered = _signals({"state": 99, "subState": 0})
    assert trigger is None
    assert recovered is None


def test_missing_substate_in_error_state_is_unknown() -> None:
    """A corrupt payload must not advance the streak."""
    trigger, recovered = _signals({"state": 7})
    assert trigger is None
    assert recovered is None


def test_debounce_requires_multiple_polls() -> None:
    assert _policy().trigger_polls >= 3


def test_has_no_fault_codes_of_its_own() -> None:
    """Raw-only policy: must never race the per-code policies."""
    assert not _policy().fault_codes
