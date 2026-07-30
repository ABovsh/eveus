"""Hardening round 2026-07-19: error sub-state re-fire + schedule attr floor."""
from __future__ import annotations

from unittest.mock import Mock

from custom_components.eveus.common_network import EveusUpdater
from custom_components.eveus.const import EVENT_ERROR
from custom_components.eveus.sensor_definitions import _make_schedule_attrs

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME


class _Hass:
    loop = None

    def __init__(self) -> None:
        self.bus = Mock()
        self.bus.async_fire = Mock()


def _updater(hass: _Hass) -> EveusUpdater:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, hass, device_number=2)
    updater._schedule_post_command_refresh = Mock()
    return updater


def _error_events(hass: _Hass) -> list[dict]:
    return [
        call.args[1]
        for call in hass.bus.async_fire.call_args_list
        if call.args[0] == EVENT_ERROR
    ]


def test_error_refires_when_substate_changes_within_error() -> None:
    """A new fault code while the charger stays in Error must reach HA."""
    hass = _Hass()
    updater = _updater(hass)
    updater._record_success(0.05, {"state": 2})
    updater._record_success(0.05, {"state": 7, "subState": 1})
    updater._record_success(0.05, {"state": 7, "subState": 3})
    events = _error_events(hass)
    assert [e["error_code"] for e in events] == [1, 3]


def test_error_does_not_refire_on_same_substate() -> None:
    hass = _Hass()
    updater = _updater(hass)
    updater._record_success(0.05, {"state": 2})
    updater._record_success(0.05, {"state": 7, "subState": 1})
    updater._record_success(0.05, {"state": 7, "subState": 1})
    assert len(_error_events(hass)) == 1


def test_error_substate_memory_resets_after_leaving_error() -> None:
    """Error → OK → same Error code again is a NEW fault and must fire."""
    hass = _Hass()
    updater = _updater(hass)
    updater._record_success(0.05, {"state": 2})
    updater._record_success(0.05, {"state": 7, "subState": 1})
    updater._record_success(0.05, {"state": 2})
    updater._record_success(0.05, {"state": 7, "subState": 1})
    assert len(_error_events(hass)) == 2


def test_error_substate_memory_survives_no_offline_gap_rule() -> None:
    """An offline gap clears state memory; re-entering Error stays silent
    on the first poll (previous is None) — unchanged contract."""
    hass = _Hass()
    updater = _updater(hass)
    updater._record_success(0.05, {"state": 2})
    updater._record_success(0.05, {"state": 7, "subState": 1})
    updater._record_failure(ValueError("offline"))
    updater._record_success(0.05, {"state": 7, "subState": 4})
    assert len(_error_events(hass)) == 1


def test_schedule_attrs_show_sub_minimum_current() -> None:
    """Firmware reports sub-7A schedule setpoints verbatim; the sensor
    attribute must display them like the Number entity does."""
    updater = Mock()
    updater.available = True
    updater.data = {
        "sh1Start": 60,
        "sh1Stop": 120,
        "sh1CurrentEnable": 1,
        "sh1CurrentValue": 6,
    }
    attrs = _make_schedule_attrs(1)(updater, None)
    assert attrs["current_limit_a"] == 6


def test_schedule_attrs_still_reject_negative_current() -> None:
    updater = Mock()
    updater.available = True
    updater.data = {"sh1CurrentEnable": 1, "sh1CurrentValue": -3}
    attrs = _make_schedule_attrs(1)(updater, None)
    assert "current_limit_a" not in attrs
