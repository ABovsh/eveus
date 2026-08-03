"""Tests for the Not Charging Reason diagnostic sensor."""
from __future__ import annotations

import pytest

from conftest import EveusTestUpdater, spec_value_fn
from custom_components.eveus.sensor_definitions import (
    NOT_CHARGING_REASON_OPTIONS,
    get_not_charging_reason,
    get_not_charging_reason_attrs,
)

# Modern firmware always sends verFWMain — the marker that tells the substate
# codes apart from firmware 1.x, whose substates mean something else.
_MODERN = {"verFWMain": "GRM070A-R3.05.4 "}


def _modern(**payload) -> EveusTestUpdater:
    return EveusTestUpdater({**_MODERN, **payload})


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"state": 4, "subState": 0}, "Charging"),
        ({"state": 0, "subState": 0}, "Starting Up"),
        ({"state": 1, "subState": 0}, "Starting Up"),
        ({"state": 2, "subState": 0}, "Cable Not Connected"),
        ({"state": 5, "subState": 0}, "Charge Complete"),
        ({"state": 7, "subState": 5}, "Error"),
        # state 3 Connected / 6 Paused: subState explains why
        ({"state": 3, "subState": 1}, "Stopped by User"),
        ({"state": 3, "subState": 2}, "Energy Limit Reached"),
        ({"state": 3, "subState": 3}, "Time Limit Reached"),
        ({"state": 3, "subState": 4}, "Cost Limit Reached"),
        ({"state": 3, "subState": 5}, "Waiting for Schedule"),
        ({"state": 3, "subState": 6}, "Schedule Energy Limit Reached"),
        ({"state": 3, "subState": 7}, "Waiting for Schedule"),
        ({"state": 3, "subState": 8}, "Schedule Energy Limit Reached"),
        ({"state": 3, "subState": 9}, "Waiting for Activation"),
        ({"state": 3, "subState": 10}, "Paused by Adaptive Mode"),
        ({"state": 3, "subState": 0}, "Waiting for Car"),
        ({"state": 6, "subState": 0}, "Paused"),
        ({"state": 20, "subState": 0}, "Unknown"),  # fw 1.x stray state
    ],
)
def test_reason_mapping(payload, expected):
    assert get_not_charging_reason(_modern(**payload), None) == expected


def test_reason_none_without_state():
    assert get_not_charging_reason(_modern(), None) is None


@pytest.mark.parametrize("state", [3, 6])
@pytest.mark.parametrize("substate", [11, 12, 99])
def test_unrecognized_substate_is_unknown_not_no_limits(state, substate):
    """A future firmware substate must not read as "nothing is holding it back".

    subState 0 genuinely means no limit is active. An unmapped non-zero code
    means some limit we cannot name IS active — reporting Waiting for Car or
    Paused there is a confident lie, the same mistake the state branch above
    already avoids by collapsing unmapped states to Unknown.
    """
    assert get_not_charging_reason(_modern(state=state, subState=substate), None) == "Unknown"


@pytest.mark.parametrize(("state", "expected"), [(3, "Waiting for Car"), (6, "Paused")])
def test_missing_substate_is_not_treated_as_an_unknown_code(state, expected):
    """A payload without the field is missing data, not an unrecognized code."""
    assert get_not_charging_reason(_modern(state=state), None) == expected


def test_every_reason_is_a_declared_option():
    """HA rejects ENUM writes outside the options list — the sets must match."""
    payloads = [
        {"state": s, "subState": sub} for s in range(8) for sub in range(13)
    ] + [{"state": 20, "subState": 0}, {"state": 3}, {"state": 6}]
    produced = {
        get_not_charging_reason(EveusTestUpdater(p), None)
        for p in payloads + [{**_MODERN, **p} for p in payloads]
    }
    produced.discard(None)
    assert produced <= set(NOT_CHARGING_REASON_OPTIONS)


def test_error_attrs_carry_error_name_and_suspend_errors():
    attrs = get_not_charging_reason_attrs(
        _modern(state=7, subState=5, suspendErrors=3), None
    )
    assert attrs == {"error": "Box Overheat", "suspend_errors": 3}


def test_attrs_empty_when_normal():
    attrs = get_not_charging_reason_attrs(
        _modern(state=4, subState=0, suspendErrors=0), None
    )
    assert attrs == {}


def test_spec_is_registered_enum():
    value_fn = spec_value_fn("not_charging_reason")
    assert value_fn(_modern(state=4, subState=0), None) == "Charging"


# =============================================================================
# Firmware 1.x (MCU_SW_version 151, GitHub issue #11)
# =============================================================================
#
# These payloads carry neither verFWMain nor its `firmware` alias — the same
# marker the coordinator uses to decide a payload needs legacy translation.
# Their substate codes are not the modern NORMAL_SUBSTATES/ERROR_STATES map,
# so the reason must come from the (already translated) device state alone
# rather than from a substate whose meaning is unknown.


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        # States the coordinator translated before the sensor sees them.
        ({"state": 2, "subState": 1}, "Cable Not Connected"),  # legacy 20
        ({"state": 4, "subState": 3}, "Charging"),  # legacy 3 + power
        # Legacy 3 with no power stays 3: a plugged-in car that is not drawing.
        ({"state": 3, "subState": 1}, "Waiting for Car"),
        ({"state": 3, "subState": 9}, "Waiting for Car"),
        ({"state": 6, "subState": 2}, "Paused"),
        ({"state": 7, "subState": 5}, "Error"),
        # An untranslated legacy code must not borrow modern substate text.
        ({"state": 20, "subState": 1}, "Unknown"),
    ],
)
def test_legacy_firmware_reason_ignores_substate(payload, expected):
    assert get_not_charging_reason(EveusTestUpdater(payload), None) == expected


def test_legacy_firmware_attrs_omit_the_error_name():
    """A 1.x fault code is not an ERROR_STATES index — do not name it."""
    attrs = get_not_charging_reason_attrs(
        EveusTestUpdater({"state": 7, "subState": 5, "suspendErrors": 3}), None
    )
    assert attrs == {"suspend_errors": 3}


def test_firmware_alias_counts_as_modern():
    """Older-but-modern payloads send `firmware` instead of verFWMain."""
    updater = EveusTestUpdater({"firmware": "3.04", "state": 3, "subState": 1})
    assert get_not_charging_reason(updater, None) == "Stopped by User"


def test_a_degraded_modern_reply_keeps_the_detailed_reason():
    """The coordinator's sticky verdict wins over one marker-less payload.

    verFWMain is not a required field, so a modern charger can drop it on a
    partial reply. Falling back to the generic state-derived reason there
    would silently downgrade the sensor with no signal that it happened.
    """
    updater = EveusTestUpdater({"state": 3, "subState": 1})
    updater.is_modern_firmware = True
    assert get_not_charging_reason(updater, None) == "Stopped by User"
    assert get_not_charging_reason_attrs(
        EveusTestUpdater({"state": 7, "subState": 5}), None
    ) == {}
