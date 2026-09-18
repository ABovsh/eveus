"""Config-flow, reauth and options regressions: model/scheme handling, form
field preservation, reload-result checking, and the single-reload rule."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
import pytest
from custom_components import eveus
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from custom_components.eveus.config_flow import build_user_data_schema, normalize_user_input
from custom_components.eveus.const import (
    CONF_MODEL,
    CONF_SOC_MODE,
    SOC_MODE_BASIC,
)
import voluptuous as vol
from conftest import (
    TEST_HOST,
    TEST_HOST_ALT,
    TEST_PASSWORD,
    TEST_USERNAME,
    wire_flow_reload_success,
)
from custom_components.eveus import config_flow
from custom_components.eveus.config_flow import (
    _safe_model_default,
)
from custom_components.eveus.const import (
    CONF_PHASES,
    CONF_SCHEME,
    DEFAULT_SCHEME,
    MODEL_16A,
    MODEL_32A,
)
from types import SimpleNamespace
from unittest.mock import Mock
from homeassistant.data_entry_flow import AbortFlow


def test_no_reloading_update_listener_is_registered() -> None:
    # The listener was removed; reconfigure/reauth reload via the helper, repair
    # and the options flow reload explicitly. Its absence prevents a double
    # unload/setup cycle on every entry update.
    assert not hasattr(eveus, "update_listener")


def test_reconfigure_repair_schema_omits_soc_mode() -> None:
    assert CONF_SOC_MODE not in build_user_data_schema({}, include_soc_mode=False).schema
    assert CONF_SOC_MODE in build_user_data_schema({}).schema


def test_reconfigure_preserves_stored_soc_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from custom_components.eveus import config_flow

    async def fake_validate_input(hass, data):
        return {
            "title": "Eveus Charger (1.2.3.4)",
            "data": normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    entry = type(
        "Entry",
        (),
        {
            "data": {
                CONF_HOST: "1.2.3.4",
                CONF_USERNAME: "u",
                CONF_PASSWORD: "p",
                CONF_MODEL: "16A",
                CONF_SOC_MODE: SOC_MODE_BASIC,
            },
            "unique_id": "1.2.3.4",
        },
    )()
    captured: dict = {}
    flow = config_flow.ConfigFlow()
    flow.hass = object()
    flow._get_reconfigure_entry = lambda: entry
    flow.async_set_unique_id = lambda unique_id: asyncio.sleep(0)
    flow._abort_if_unique_id_configured = lambda: None
    from conftest import wire_flow_reload_success

    wire_flow_reload_success(flow, entry, captured)
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    asyncio.run(
        flow.async_step_reconfigure(
            {
                CONF_HOST: "1.2.3.4",
                CONF_USERNAME: "u",
                CONF_PASSWORD: "p",
                CONF_MODEL: "16A",
            }
        )
    )

    # normalize_user_input would default an absent soc_mode to advanced; the flow
    # re-asserts the entry's stored mode so reconfigure can't silently flip it.
    assert captured["data"][CONF_SOC_MODE] == SOC_MODE_BASIC

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


def test_safe_model_default_rejects_unknown_model() -> None:
    assert _safe_model_default("some-corrupt-value") == MODEL_16A
    assert _safe_model_default(None) == MODEL_16A
    assert _safe_model_default(MODEL_32A) == MODEL_32A


def test_build_user_data_schema_sanitizes_invalid_model_default() -> None:
    schema = build_user_data_schema({CONF_MODEL: "not-a-real-model"}, include_soc_mode=False)
    assert _schema_default(schema, CONF_MODEL) == MODEL_16A


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
    committed: dict = {}
    wire_flow_reload_success(flow, entry, committed)
    flow._migrate_device_identifiers = lambda entry, old, new: None
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    result = asyncio.run(
        flow.async_step_reconfigure(_input(**{CONF_HOST: TEST_HOST_ALT}))
    )

    assert captured[CONF_SCHEME] == DEFAULT_SCHEME == "http"
    assert result["reason"] == "reconfigure_successful"
    assert committed["data"][CONF_SCHEME] == "http"


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
    committed: dict = {}
    wire_flow_reload_success(flow, entry, committed)
    flow._migrate_device_identifiers = lambda entry, old, new: None
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    # user_input carries only a bare host (no "https://" prefix) — exactly the
    # shape the real form submits when CONF_SCHEME isn't a field on it.
    result = asyncio.run(
        flow.async_step_reconfigure(_input(**{CONF_HOST: TEST_HOST_ALT}))
    )

    assert captured[CONF_SCHEME] == "https"
    assert result["reason"] == "reconfigure_successful"
    assert committed["data"][CONF_SCHEME] == "https"


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

def _flow_input(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        CONF_HOST: TEST_HOST,
        CONF_USERNAME: TEST_USERNAME,
        CONF_PASSWORD: TEST_PASSWORD,
        CONF_MODEL: MODEL_16A,
    }
    data.update(overrides)
    return data


def _entry(**data_overrides: object) -> SimpleNamespace:
    return SimpleNamespace(
        entry_id="eid-1",
        data=_flow_input(**data_overrides),
        unique_id=TEST_HOST,
    )


def _flow_with_hass(entry: SimpleNamespace, *, reload_ok: bool) -> config_flow.ConfigFlow:
    flow = config_flow.ConfigFlow()

    def fake_update_entry(target, **kwargs):
        if "data" in kwargs:
            target.data = dict(kwargs["data"])
        if "unique_id" in kwargs:
            target.unique_id = kwargs["unique_id"]
        if "title" in kwargs:
            target.title = kwargs["title"]
        return True

    flow.hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_update_entry=Mock(side_effect=fake_update_entry),
            async_reload=AsyncMock(return_value=reload_ok),
            async_schedule_reload=Mock(),
        )
    )
    flow.async_set_unique_id = lambda unique_id: asyncio.sleep(0)
    flow._abort_if_unique_id_configured = lambda: None
    flow.async_abort = lambda reason: {"type": "abort", "reason": reason}
    # Legacy helper stub (same shape existing tests use): if the flow still
    # routes through the fire-and-forget helper, it "succeeds" here without
    # ever awaiting async_reload -- which is exactly the defect.
    flow.async_update_reload_and_abort = lambda entry, **kwargs: {
        "type": "abort",
        "reason": "reconfigure_successful"
        if flow._get_reauth_entry is None
        else "reauth_successful",
        **kwargs,
    }
    flow._get_reauth_entry = None
    return flow


def _fake_validate(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_validate_input(hass, data):
        return {
            "title": f"Eveus Charger ({data[CONF_HOST]})",
            "data": normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)


def _run_reconfigure(monkeypatch: pytest.MonkeyPatch, *, reload_ok: bool):
    _fake_validate(monkeypatch)
    entry = _entry()
    flow = _flow_with_hass(entry, reload_ok=reload_ok)
    flow._get_reconfigure_entry = lambda: entry
    flow._migrate_device_identifiers = lambda entry, old, new: None
    result = asyncio.run(
        flow.async_step_reconfigure(_flow_input(**{CONF_HOST: TEST_HOST_ALT}))
    )
    return entry, flow, result


def _run_reauth(monkeypatch: pytest.MonkeyPatch, *, reload_ok: bool):
    _fake_validate(monkeypatch)
    entry = _entry(**{CONF_USERNAME: "old", CONF_PASSWORD: "old"})
    flow = _flow_with_hass(entry, reload_ok=reload_ok)
    flow._get_reauth_entry = lambda: entry
    result = asyncio.run(
        flow.async_step_reauth_confirm(
            {CONF_USERNAME: TEST_USERNAME, CONF_PASSWORD: "new-secret"}
        )
    )
    return entry, flow, result


def test_reconfigure_failed_reload_aborts_reload_failed_but_keeps_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry, flow, result = _run_reconfigure(monkeypatch, reload_ok=False)
    assert result["type"] == "abort"
    assert result["reason"] == "reload_failed"
    # The data change is committed either way; only the success claim is gated.
    assert entry.data[CONF_HOST] == TEST_HOST_ALT
    flow.hass.config_entries.async_reload.assert_awaited_once_with("eid-1")


def test_reconfigure_successful_reload_aborts_with_success_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry, flow, result = _run_reconfigure(monkeypatch, reload_ok=True)
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == TEST_HOST_ALT
    flow.hass.config_entries.async_reload.assert_awaited_once_with("eid-1")


def test_reauth_failed_reload_aborts_reload_failed_but_keeps_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry, flow, result = _run_reauth(monkeypatch, reload_ok=False)
    assert result["type"] == "abort"
    assert result["reason"] == "reload_failed"
    assert entry.data[CONF_PASSWORD] == "new-secret"
    flow.hass.config_entries.async_reload.assert_awaited_once_with("eid-1")


def test_reauth_successful_reload_aborts_with_success_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry, flow, result = _run_reauth(monkeypatch, reload_ok=True)
    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new-secret"
    flow.hass.config_entries.async_reload.assert_awaited_once_with("eid-1")

def test_reauth_confirm_propagates_abort_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """A concurrent flow on the same host aborts, not "unknown"."""

    async def fake_validate_input(hass, data):
        return {
            "title": f"Eveus Charger ({TEST_HOST})",
            "data": normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    async def raise_already_in_progress(unique_id):
        raise AbortFlow("already_in_progress")

    entry = type(
        "Entry",
        (),
        {
            "data": {
                CONF_HOST: TEST_HOST,
                CONF_USERNAME: "old",
                CONF_PASSWORD: "old",
                CONF_MODEL: MODEL_16A,
            },
            "unique_id": TEST_HOST,
        },
    )()
    flow = config_flow.ConfigFlow()
    flow.hass = object()
    flow._get_reauth_entry = lambda: entry
    flow.async_set_unique_id = raise_already_in_progress
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    with pytest.raises(AbortFlow) as excinfo:
        asyncio.run(
            flow.async_step_reauth_confirm(
                {CONF_USERNAME: TEST_USERNAME, CONF_PASSWORD: TEST_PASSWORD}
            )
        )

    assert excinfo.value.reason == "already_in_progress"
