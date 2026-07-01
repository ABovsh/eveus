"""Regression tests for the 2026-07-01 deep-audit hardening round (rc branch).

Covers: C03 (model-default sanitization), C04 (reconfigure scheme
preservation — must never force https, only preserve whatever scheme was
already stored), C05 (setup-error form field preservation), D03 (Target SOC
= 0 no longer stops charging instantly), D04 (bool rejection on SOC-number
restore), D05 (suspendLimits recheck immediately before the Stop POST), B01
(OverflowError on numeric coercion), B02 (wifi_rssi failure isolation).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from conftest import TEST_HOST, TEST_HOST_ALT, TEST_PASSWORD, TEST_USERNAME

from custom_components.eveus import config_flow
from custom_components.eveus.config_flow import (
    _safe_model_default,
    build_user_data_schema,
    normalize_user_input,
)
from custom_components.eveus.const import (
    CONF_MODEL,
    CONF_PHASES,
    CONF_SCHEME,
    DEFAULT_SCHEME,
    MODEL_16A,
    MODEL_32A,
)


def _input(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        CONF_HOST: TEST_HOST,
        CONF_USERNAME: TEST_USERNAME,
        CONF_PASSWORD: TEST_PASSWORD,
        CONF_MODEL: MODEL_16A,
    }
    data.update(overrides)
    return data


def _schema_default(schema: vol.Schema, key: str):
    marker = next(k for k in schema.schema if k.schema == key)
    return marker.default()


# ---------------------------------------------------------------------------
# C03: an invalid stored CONF_MODEL must not seed vol.In(MODELS) outside its
# own allowed set.
# ---------------------------------------------------------------------------

def test_safe_model_default_rejects_unknown_model() -> None:
    assert _safe_model_default("some-corrupt-value") == MODEL_16A
    assert _safe_model_default(None) == MODEL_16A
    assert _safe_model_default(MODEL_32A) == MODEL_32A


def test_build_user_data_schema_sanitizes_invalid_model_default() -> None:
    schema = build_user_data_schema({CONF_MODEL: "not-a-real-model"}, include_soc_mode=False)
    assert _schema_default(schema, CONF_MODEL) == MODEL_16A


# ---------------------------------------------------------------------------
# C04: reconfigure must preserve whatever scheme was already stored (most
# commonly "http", since most users configure a bare IP) — it must never
# force/assume https, only avoid silently downgrading an existing https entry.
# ---------------------------------------------------------------------------

def test_reconfigure_preserves_stored_http_scheme_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The common case: entry has no explicit https, host is a bare IP with no
    scheme prefix. Reconfiguring must keep it on http, not add any scheme
    enforcement.
    """
    captured: dict = {}

    async def fake_validate_input(hass, data):
        captured.update(data)
        return {
            "title": f"Eveus Charger ({TEST_HOST_ALT})",
            "data": normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    entry = type(
        "Entry",
        (),
        {"data": _input(**{CONF_HOST: TEST_HOST}), "unique_id": TEST_HOST},
    )()
    flow = config_flow.ConfigFlow()
    flow.hass = object()
    flow._get_reconfigure_entry = lambda: entry
    flow.async_set_unique_id = lambda unique_id: asyncio.sleep(0)
    flow._abort_if_unique_id_configured = lambda: None
    flow.async_update_reload_and_abort = lambda entry, **kwargs: {
        "type": "abort",
        "reason": "reconfigure_successful",
        **kwargs,
    }
    flow._migrate_device_identifiers = lambda entry, old, new: None
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = asyncio.run(
        flow.async_step_reconfigure(_input(**{CONF_HOST: TEST_HOST_ALT}))
    )

    assert captured[CONF_SCHEME] == DEFAULT_SCHEME == "http"
    assert result["data"][CONF_SCHEME] == "http"


def test_reconfigure_keeps_https_when_host_edited_without_retyping_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for C04: an https entry must not be silently downgraded
    to http just because the reconfigure form's user_input never carries
    CONF_SCHEME (it's inferred from the host string, which the user may have
    edited without retyping "https://").
    """
    captured: dict = {}

    async def fake_validate_input(hass, data):
        captured.update(data)
        return {
            "title": f"Eveus Charger ({TEST_HOST_ALT})",
            "data": normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    entry = type(
        "Entry",
        (),
        {
            "data": _input(**{CONF_HOST: TEST_HOST, CONF_SCHEME: "https"}),
            "unique_id": TEST_HOST,
        },
    )()
    flow = config_flow.ConfigFlow()
    flow.hass = object()
    flow._get_reconfigure_entry = lambda: entry
    flow.async_set_unique_id = lambda unique_id: asyncio.sleep(0)
    flow._abort_if_unique_id_configured = lambda: None
    flow.async_update_reload_and_abort = lambda entry, **kwargs: {
        "type": "abort",
        "reason": "reconfigure_successful",
        **kwargs,
    }
    flow._migrate_device_identifiers = lambda entry, old, new: None
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    # user_input carries only a bare host (no "https://" prefix) — exactly the
    # shape the real form submits when CONF_SCHEME isn't a field on it.
    result = asyncio.run(
        flow.async_step_reconfigure(_input(**{CONF_HOST: TEST_HOST_ALT}))
    )

    assert captured[CONF_SCHEME] == "https"
    assert result["data"][CONF_SCHEME] == "https"


# ---------------------------------------------------------------------------
# C05: a setup validation error must preserve the submitted form fields
# (host/username/model/phases), not wipe them back to the blank schema.
# ---------------------------------------------------------------------------

def test_setup_error_preserves_submitted_form_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_validate_input(hass, data):
        raise config_flow.CannotConnect

    flow = config_flow.ConfigFlow()
    flow.hass = object()
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    submitted = _input(**{CONF_HOST: TEST_HOST_ALT, CONF_PHASES: 3})
    result = asyncio.run(flow.async_step_user(submitted))

    assert result["errors"] == {"base": "cannot_connect"}
    schema = result["data_schema"]
    assert _schema_default(schema, CONF_HOST) == TEST_HOST_ALT
    assert _schema_default(schema, CONF_PHASES) == 3
    assert _schema_default(schema, CONF_MODEL) == MODEL_16A


# ---------------------------------------------------------------------------
# D03 / D04 / D05: soc_limit.py and number.py hardening
# ---------------------------------------------------------------------------

def test_zero_target_soc_does_not_stop_charging_at_session_start():
    from custom_components.eveus.ev_sensors import CachedSOCCalculator
    from custom_components.eveus.soc_limit import SocLimitController

    calc = CachedSOCCalculator()
    calc.set_value("initial_soc", 20)
    calc.set_value("battery_capacity", 50)
    calc.set_value("soc_correction", 0)
    calc.set_value("target_soc", 0)

    updater = MagicMock()
    updater.available = True
    updater.last_update_success = True
    updater.device_number = 1
    updater.data = {
        "state": 4,
        "sessionEnergy": 0.0,
        "evseEnabled": 0,
        "suspendLimits": 0,
    }
    updater.send_command = AsyncMock(return_value=True)

    hass = MagicMock()
    hass.async_create_task = MagicMock()
    hass.bus.async_fire = MagicMock()

    ctrl = SocLimitController(hass, updater, calc)
    ctrl.set_enabled(True)
    ctrl.process()

    hass.async_create_task.assert_not_called()
    hass.bus.async_fire.assert_not_called()


def test_stop_aborts_if_suspend_limits_enabled_mid_flight():
    """Regression test for D05: process() only checked suspendLimits when it
    scheduled the Stop task; if "Disable limits" was toggled on while the task
    was queued, _stop() must recheck before POSTing rather than sending a
    command the safety contract says to stand down from.
    """
    from custom_components.eveus.ev_sensors import CachedSOCCalculator
    from custom_components.eveus.soc_limit import SocLimitController

    calc = CachedSOCCalculator()
    calc.set_value("initial_soc", 20)
    calc.set_value("battery_capacity", 50)
    calc.set_value("soc_correction", 0)
    calc.set_value("target_soc", 80)

    updater = MagicMock()
    updater.available = True
    updater.last_update_success = True
    updater.device_number = 1
    updater.data = {
        "state": 4,
        "sessionEnergy": 30.0,
        "evseEnabled": 0,
        "suspendLimits": 0,
    }
    updater.send_command = AsyncMock(return_value=True)

    hass = MagicMock()

    def _spawn(coro):
        # Simulate "Disable limits" being toggled on after process() decided
        # to stop but before the queued task actually runs.
        updater.data = {**updater.data, "suspendLimits": 1}
        asyncio.run(coro)

    hass.async_create_task = _spawn
    hass.bus.async_fire = MagicMock()

    ctrl = SocLimitController(hass, updater, calc)
    ctrl.set_enabled(True)
    ctrl.process()

    updater.send_command.assert_not_called()
    hass.bus.async_fire.assert_not_called()


def test_soc_number_restore_rejects_bool_native_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test for D04: bool is an int subclass, so float(False) == 0.0
    with no exception — a corrupt RestoreNumber state of False must not sneak
    through as a valid in-range restored value (e.g. Target SOC silently
    becoming 0, which per D03 would otherwise stop charging instantly).
    """
    from conftest import EveusTestUpdater, HelperHass
    from custom_components.eveus import number as number_module
    from custom_components.eveus.ev_sensors import CachedSOCCalculator
    from custom_components.eveus.number import EveusTargetSocNumber

    async def noop_super(self):
        return None

    monkeypatch.setattr(number_module.BaseEveusEntity, "async_added_to_hass", noop_super)
    monkeypatch.setattr(number_module, "async_dispatcher_send", lambda *a, **k: None)

    updater = EveusTestUpdater({})
    calc = CachedSOCCalculator()
    entity = EveusTargetSocNumber(updater, calc, seed=80, device_number=1)
    entity.hass = HelperHass({})

    async def fake_last(self):
        from types import SimpleNamespace

        return SimpleNamespace(native_value=False)

    monkeypatch.setattr(type(entity), "async_get_last_number_data", fake_last, raising=False)

    asyncio.run(number_module.EveusSocConfigNumber.async_added_to_hass(entity))

    assert entity.native_value == 80  # seed kept, bool restore rejected
    assert calc.target_soc == 80


# ---------------------------------------------------------------------------
# B01 / B02: sensor_definitions.py numeric coercion + attribute isolation
# ---------------------------------------------------------------------------

def test_value_getter_rejects_overflow_error():
    """Regression test for B01: float() on an absurdly large int raises
    OverflowError, not TypeError/ValueError — the coercion must catch it too,
    matching every other numeric getter in this module.
    """
    from conftest import EveusTestUpdater
    from custom_components.eveus.sensor_definitions import _make_value_getter

    getter = _make_value_getter("powerMeas")
    updater = EveusTestUpdater({"powerMeas": 10**400})

    assert getter(updater, None) is None


def test_connection_attrs_isolates_wifi_rssi_failure(monkeypatch: pytest.MonkeyPatch):
    """Regression test for B02: a failure fetching the optional wifi_rssi must
    only drop that one field, not replace the whole (already-valid)
    connection_quality/latency_avg/status dict with {"status": "Error"}.
    """
    from custom_components.eveus import sensor_definitions as sd

    updater = MagicMock()
    updater.available = True
    updater.connection_quality = {"success_rate": 75, "latency_avg": 0.42}

    def boom(updater, hass):
        raise RuntimeError("boom")

    monkeypatch.setattr(sd, "get_wifi_rssi", boom)

    attrs = sd.get_connection_attrs(updater, None)

    assert attrs["status"] == "Fair"
    assert attrs["connection_quality"] == 75
    assert "wifi_rssi" not in attrs
