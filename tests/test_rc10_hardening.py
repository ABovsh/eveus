"""Hardening tests for 4.10 rc10.

Covers corrupt-stored-data and restore-path edge cases that the live-data
guards did not yet cover:

  * F03 — a malformed (infinite) device_number on one entry no longer crashes
    device-number assignment/lookup for every other Eveus entry
  * F02 — an infinite stored phase count no longer crashes reconfigure/repair
    schema construction
  * F01 — non-string stored credentials surface a repairable validation error
    instead of an unhandled TypeError/AttributeError
  * F08 — a restored non-finite cost value (nan/inf) is dropped, so the next
    real meter reset still advances last_reset (correct monetary statistics)
  * F07 — a restored non-datetime last_reset attribute is ignored instead of
    being assigned to a datetime-typed field
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import voluptuous as vol

from conftest import EveusTestUpdater, disable_state_writes
from custom_components.eveus import config_flow, utils
from custom_components.eveus.config_flow import validate_credentials
from custom_components.eveus.const import DEFAULT_PHASES
from custom_components.eveus.sensor_definitions import create_sensor_specifications


class _Entry:
    def __init__(self, device_number) -> None:
        self.data = {}
        if device_number is not None:
            self.data["device_number"] = device_number


class _ConfigEntries:
    def __init__(self, entries) -> None:
        self._entries = entries

    def async_entries(self, domain: str):
        assert domain == "eveus"
        return self._entries


class _Hass:
    def __init__(self, entries) -> None:
        self.config_entries = _ConfigEntries(entries)


# ---------------------------------------------------------------------------
# F03 — infinite device_number on a sibling entry must not crash assignment
# ---------------------------------------------------------------------------

def test_infinite_device_number_on_other_entry_does_not_crash() -> None:
    hass = _Hass([_Entry(1), _Entry(float("inf")), _Entry("bad")])

    # int(float("inf")) raises OverflowError; the scan must skip it, not crash.
    assert utils.get_next_device_number(hass) == 2
    assert utils.is_device_number_taken(hass, 1) is True
    assert utils.is_device_number_taken(hass, 5) is False


# ---------------------------------------------------------------------------
# F02 — infinite stored phase count must not crash the schema default
# ---------------------------------------------------------------------------

def test_safe_phases_default_handles_infinite_value() -> None:
    assert config_flow._safe_phases_default(float("inf")) == DEFAULT_PHASES
    assert config_flow._safe_phases_default(float("nan")) == DEFAULT_PHASES
    # Building the reconfigure schema with corrupt stored phases must not raise.
    config_flow.build_user_data_schema({"phases": float("inf")})


# ---------------------------------------------------------------------------
# F01 — non-string stored credentials raise a repairable vol.Invalid
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "username,password",
    [
        (12345, "pw"),
        ("user", 12345),
        (None, "pw"),
        (b"user", "pw"),
    ],
)
def test_validate_credentials_rejects_non_string_values(username, password) -> None:
    with pytest.raises(vol.Invalid):
        validate_credentials(username, password)


# ---------------------------------------------------------------------------
# F07 / F08 — cost-sensor restore path rejects bad attribute/state values
# ---------------------------------------------------------------------------

def _cost_sensor():
    for spec in create_sensor_specifications():
        if spec.key == "session_cost":
            entity = spec.create_sensor(EveusTestUpdater(data={}), 1)
            disable_state_writes(entity)
            entity.hass = None
            return entity
    raise AssertionError("session_cost spec not found")


def _restore(entity, state) -> None:
    asyncio.run(entity._async_restore_state(state))


def test_restore_rejects_non_finite_prev_cost_value() -> None:
    entity = _cost_sensor()
    state = SimpleNamespace(state="inf", attributes={})

    _restore(entity, state)

    # inf would make `value < prev` always True and corrupt reset detection.
    assert entity._prev_cost_value is None


def test_restore_accepts_finite_prev_cost_value() -> None:
    entity = _cost_sensor()
    state = SimpleNamespace(state="4.32", attributes={})

    _restore(entity, state)

    assert entity._prev_cost_value == pytest.approx(4.32)


def test_restore_ignores_non_datetime_last_reset() -> None:
    entity = _cost_sensor()
    state = SimpleNamespace(state="4.32", attributes={"last_reset": 1234567890})

    _restore(entity, state)

    assert entity._attr_last_reset is None


def test_restore_accepts_datetime_last_reset() -> None:
    entity = _cost_sensor()
    reset = datetime(2026, 1, 1, tzinfo=timezone.utc)
    state = SimpleNamespace(state="4.32", attributes={"last_reset": reset})

    _restore(entity, state)

    assert entity._attr_last_reset == reset
