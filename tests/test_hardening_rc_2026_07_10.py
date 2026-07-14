"""Hardening round 2026-07-10 (rc audit): Last Session bounds, schedule
sub-minimum reads, options-flow reload result, setup OverflowError tolerance."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from custom_components.eveus import config_flow
from custom_components.eveus.common_network import EveusUpdater
from custom_components.eveus.const import EVENT_CHARGING_FINISHED, SOC_MODE_BASIC
from custom_components.eveus.number import EveusSetpointNumber, SCHEDULE_LIMIT_NUMBERS
from custom_components.eveus.session_history import LastSessionEnergySensor

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME

CONF_SOC_MODE = "soc_mode"


# =========================================================================
# B-F01 — charging_finished event emission bounds (common_network.py)
# =========================================================================


class _Hass:
    loop = None

    def __init__(self) -> None:
        self.bus = Mock()
        self.bus.async_fire = Mock()


def _updater(hass: _Hass) -> EveusUpdater:
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, hass, device_number=1)
    updater._schedule_post_command_refresh = Mock()
    return updater


def _finished_events(hass: _Hass) -> list[dict]:
    return [
        call.args[1]
        for call in hass.bus.async_fire.call_args_list
        if call.args[0] == EVENT_CHARGING_FINISHED
    ]


def _finish_session(charging_payload: dict) -> list[dict]:
    hass = _Hass()
    updater = _updater(hass)
    updater._record_success(0.05, {"state": 4, **charging_payload})
    updater._record_success(0.05, {"state": 5})
    return _finished_events(hass)


def test_finished_event_drops_negative_session_energy() -> None:
    events = _finish_session({"sessionEnergy": -5.0, "sessionMoney": 12.5})
    assert len(events) == 1
    assert events[0]["session_energy_kwh"] is None
    assert events[0]["session_cost"] == pytest.approx(12.5)


def test_finished_event_drops_absurd_session_cost_and_duration() -> None:
    events = _finish_session(
        {"sessionEnergy": 18.4, "sessionMoney": 1e12, "sessionTime": -30}
    )
    assert len(events) == 1
    assert events[0]["session_energy_kwh"] == pytest.approx(18.4)
    assert events[0]["session_cost"] is None
    assert events[0]["session_duration_s"] is None


def test_finished_event_keeps_valid_snapshot_values() -> None:
    events = _finish_session(
        {"sessionEnergy": 18.4, "sessionMoney": 49.78, "sessionTime": 22320}
    )
    assert len(events) == 1
    assert events[0]["session_energy_kwh"] == pytest.approx(18.4)
    assert events[0]["session_cost"] == pytest.approx(49.78)
    assert events[0]["session_duration_s"] == 22320


# =========================================================================
# B-F01 — Last Session sensor capture + restore bounds (session_history.py)
# =========================================================================


def _session_sensor() -> LastSessionEnergySensor:
    updater = MagicMock()
    updater.device_number = 1
    return LastSessionEnergySensor(updater, 1)


def _event(**data) -> SimpleNamespace:
    payload = {
        "device_number": 1,
        "reason": "complete",
        "session_energy_kwh": 18.46,
        "session_cost": 49.78,
        "session_duration_s": 22320,
    }
    payload.update(data)
    return SimpleNamespace(data=payload)


@pytest.mark.parametrize("bad", [-1.0, 2_000_000.0, True, "18.46", float("inf")])
def test_last_session_sensor_rejects_out_of_domain_event_values(bad) -> None:
    sensor = _session_sensor()
    sensor._handle_finished_event(_event(session_energy_kwh=bad))
    assert sensor.native_value is None


def test_last_session_sensor_still_captures_valid_value() -> None:
    sensor = _session_sensor()
    sensor._handle_finished_event(_event())
    assert sensor.native_value == pytest.approx(18.46)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_state", ["inf", "nan", "-3", "1e12"])
async def test_restore_rejects_non_finite_and_out_of_range(bad_state: str) -> None:
    sensor = _session_sensor()
    state = SimpleNamespace(state=bad_state, attributes={"reason": "complete"})
    await sensor._async_restore_state(state)
    assert sensor.native_value is None


@pytest.mark.asyncio
async def test_restore_keeps_valid_value() -> None:
    sensor = _session_sensor()
    state = SimpleNamespace(state="18.46", attributes={"reason": "complete"})
    await sensor._async_restore_state(state)
    assert sensor.native_value == pytest.approx(18.46)


# =========================================================================
# D-F01 — schedule current limits accept sub-minimum device readings
# (probe-verified 2026-07-10: firmware reports sh1CurrentValue=6 verbatim)
# =========================================================================

_SCHEDULE_CURRENT = next(
    d for d in SCHEDULE_LIMIT_NUMBERS if d.key == "schedule_1_current_limit"
)


def _schedule_number() -> tuple[EveusSetpointNumber, MagicMock]:
    updater = MagicMock()
    updater.available = True
    updater.data = {"sh1CurrentValue": 6}
    updater.config_entry = MagicMock()
    ent = EveusSetpointNumber(updater, _SCHEDULE_CURRENT, device_number=1, max_value=16.0)
    ent.hass = MagicMock()
    ent.async_write_ha_state = MagicMock()
    return ent, updater


def test_schedule_current_reads_sub_minimum_device_value() -> None:
    ent, _ = _schedule_number()
    assert ent._read_device_value() == pytest.approx(6.0)


def test_schedule_current_still_rejects_negative_and_over_max() -> None:
    ent, updater = _schedule_number()
    updater.data = {"sh1CurrentValue": -1}
    assert ent._read_device_value() is None
    updater.data = {"sh1CurrentValue": 99}
    assert ent._read_device_value() is None


@pytest.mark.asyncio
async def test_schedule_current_restores_sub_minimum_value() -> None:
    ent, updater = _schedule_number()
    updater.available = False
    updater.data = {}
    await ent._async_restore_state(SimpleNamespace(state="6", attributes={}))
    assert ent._attr_native_value == pytest.approx(6.0)


# =========================================================================
# C-F02 — options flow surfaces a failed reload instead of claiming success
# =========================================================================


def _options_flow(reload_result):
    entry = type(
        "Entry",
        (),
        {
            "data": {
                "host": TEST_HOST,
                "username": TEST_USERNAME,
                "password": TEST_PASSWORD,
                "model": "16A",
                CONF_SOC_MODE: "advanced",
                "battery_capacity": 75.0,
                "soc_correction": 9.5,
            },
            "entry_id": "opt-entry",
        },
    )()

    class _ConfigEntries:
        def async_update_entry(self, entry, *, data):
            entry.data = data

        async def async_reload(self, entry_id):
            return reload_result

    flow = config_flow.EveusOptionsFlow(entry)
    flow.hass = SimpleNamespace(config_entries=_ConfigEntries())
    flow.async_create_entry = lambda *, title, data: {"type": "create_entry"}
    flow.async_abort = lambda *, reason: {"type": "abort", "reason": reason}
    return flow


def test_options_apply_reports_failed_reload() -> None:
    flow = _options_flow(reload_result=False)
    result = asyncio.run(flow.async_step_init({CONF_SOC_MODE: SOC_MODE_BASIC}))
    assert result["type"] == "abort"
    assert result["reason"] == "reload_failed"


def test_options_apply_succeeds_on_reload_true() -> None:
    flow = _options_flow(reload_result=True)
    result = asyncio.run(flow.async_step_init({CONF_SOC_MODE: SOC_MODE_BASIC}))
    assert result["type"] == "create_entry"


# =========================================================================
# C-F03 — validate_device_response tolerates a float-overflowing currentSet
# =========================================================================


def test_validate_device_response_tolerates_huge_currentset() -> None:
    payload = {"state": 2, "currentSet": 10**400}
    info = config_flow.validate_device_response(payload, "16A")
    assert info["current_set"] is None
