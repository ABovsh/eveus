"""Hardening tests for 4.9.2-rc7: payload schema/integer strictness, log privacy,
telemetry sanity bounds."""
from __future__ import annotations

import asyncio
import inspect
import json

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from conftest import EveusTestUpdater, TEST_HOST, TEST_PASSWORD, TEST_USERNAME
from custom_components.eveus import common_command, common_network, config_flow
from custom_components.eveus import sensor_definitions as sd
from custom_components.eveus import utils
from custom_components.eveus.common_network import EveusUpdater


class _Hass:
    loop = None


class _Response:
    def __init__(self, *, status: int = 200, payload: object = None) -> None:
        self.status = status
        self.payload = payload if payload is not None else {"state": 2, "currentSet": 16}

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def json(self, **kwargs: object) -> object:
        if isinstance(self.payload, str):
            return json.loads(self.payload)
        return self.payload


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response

    def post(self, url: str, **kwargs: object) -> _Response:
        return self.response


def _run_update(payload: object, monkeypatch: pytest.MonkeyPatch):
    session = _Session(_Response(payload=payload))
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    return asyncio.run(updater._async_update_data())


# ---------------------------------------------------------------------------
# F01 — runtime /main validation requires currentSet (matches config-flow contract)
# ---------------------------------------------------------------------------

def test_coordinator_rejects_payload_without_current_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(UpdateFailed):
        _run_update({"state": 2}, monkeypatch)


def test_coordinator_accepts_payload_with_current_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _run_update({"state": 2, "currentSet": 16}, monkeypatch)
    assert data == {"state": 2, "currentSet": 16}


# ---------------------------------------------------------------------------
# F02 / F04 — non-integer state is rejected, not truncated
# ---------------------------------------------------------------------------

def test_coordinator_rejects_fractional_state(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(UpdateFailed):
        _run_update({"state": 4.9, "currentSet": 16}, monkeypatch)


def test_config_flow_rejects_fractional_state() -> None:
    with pytest.raises(config_flow.InvalidDevice):
        config_flow.validate_device_response({"state": 2.9, "currentSet": 16}, "16A")


# ---------------------------------------------------------------------------
# F03 — config flow rejects boolean state (int(True) == 1 would sneak through)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [True, False])
def test_config_flow_rejects_boolean_state(bad: bool) -> None:
    with pytest.raises(config_flow.InvalidDevice):
        config_flow.validate_device_response({"state": bad, "currentSet": 16}, "16A")


# ---------------------------------------------------------------------------
# F07–F15 — get_safe_value rejects non-integral floats for the int converter
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [4.9, 0.9, 1.2, -0.5])
def test_get_safe_value_int_rejects_fractional_float(bad: float) -> None:
    assert utils.get_safe_value({"x": bad}, "x", int) is None


@pytest.mark.parametrize("good,expected", [(4.0, 4), (0.0, 0), (16, 16), ("7", 7)])
def test_get_safe_value_int_accepts_integral_values(good: object, expected: int) -> None:
    assert utils.get_safe_value({"x": good}, "x", int) == expected


def test_get_safe_value_float_still_accepts_fractional() -> None:
    assert utils.get_safe_value({"x": 4.9}, "x", float) == 4.9


def test_switch_state_getter_rejects_fractional() -> None:
    # A fractional evseEnabled must not truncate to a definite on/off.
    assert utils.get_safe_value({"evseEnabled": 0.9}, "evseEnabled", int) is None


def test_ground_status_ignores_fractional() -> None:
    updater = EveusTestUpdater({"ground": 0.9})
    assert sd.get_ground_status(updater, None) is None


def test_adaptive_status_ignores_fractional() -> None:
    updater = EveusTestUpdater({"aiStatus": 1.9})
    assert sd.get_adaptive_charging_state(updater, None) is None


# ---------------------------------------------------------------------------
# F06 — command-failure log records the error type, not the exception string
# ---------------------------------------------------------------------------

def test_command_failure_log_uses_error_type_not_repr() -> None:
    src = inspect.getsource(common_command.CommandManager.send_command)
    assert "type(last_error).__name__" in src


# ---------------------------------------------------------------------------
# F17 / F18 / F19 — live telemetry getters reject impossible upper outliers
# ---------------------------------------------------------------------------

def test_voltage_getter_rejects_outlier() -> None:
    assert sd.get_voltage(EveusTestUpdater({"voltMeas1": 99999}), None) is None
    assert sd.get_voltage(EveusTestUpdater({"voltMeas1": 230}), None) == 230


def test_current_getter_rejects_outlier() -> None:
    assert sd.get_current(EveusTestUpdater({"curMeas1": 999}), None) is None
    assert sd.get_current(EveusTestUpdater({"curMeas1": 16}), None) == 16


def test_power_getter_rejects_outlier() -> None:
    assert sd.get_power(EveusTestUpdater({"powerMeas": 999999}), None) is None
    assert sd.get_power(EveusTestUpdater({"powerMeas": 7200}), None) == 7200


def test_current_set_getter_rejects_above_model_max() -> None:
    assert sd.get_current_set(EveusTestUpdater({"currentSet": 999}), None) is None


def test_adaptive_telemetry_rejects_outliers() -> None:
    assert sd.get_adaptive_current(EveusTestUpdater({"aiModecurrent": 999}), None) is None
    assert sd.get_adaptive_voltage(EveusTestUpdater({"aiVoltage": 99999}), None) is None


# ---------------------------------------------------------------------------
# F20 — schedule energy cap drops implausibly large values
# ---------------------------------------------------------------------------

def test_schedule_energy_limit_drops_outlier() -> None:
    attrs_fn = sd._make_schedule_attrs(1)
    updater = EveusTestUpdater(
        {"sh1EnergyEnable": 1, "sh1EnergyValue": 1_000_000_000}
    )
    assert "energy_limit_kwh" not in attrs_fn(updater, None)


def test_schedule_energy_limit_keeps_reasonable_value() -> None:
    attrs_fn = sd._make_schedule_attrs(1)
    updater = EveusTestUpdater({"sh1EnergyEnable": 1, "sh1EnergyValue": 50})
    assert attrs_fn(updater, None)["energy_limit_kwh"] == 50
