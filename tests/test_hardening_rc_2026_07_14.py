"""Hardening round 2026-07-14 (rc audit): /init firmware fallback integer
bounds, reconfigure/reauth reload result checking, device-trigger number
fallback from entry data."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from conftest import TEST_HOST, TEST_HOST_ALT, TEST_PASSWORD, TEST_USERNAME
from custom_components.eveus import common_network, config_flow
from custom_components.eveus.common_network import EveusUpdater
from custom_components.eveus.config_flow import CONF_MODEL, normalize_user_input
from custom_components.eveus.const import MODEL_16A
from test_init_firmware_fallback import _MultiSession, _Response, FW151_MAIN


# =========================================================================
# D1 — /init firmware fallback: unbounded ints must not raise OverflowError
# (common_network.py async_maybe_fetch_init_firmware)
# =========================================================================


class _Hass:
    loop = None


def _fw_fallback_for(monkeypatch: pytest.MonkeyPatch, raw_version: object) -> str | None:
    session = _MultiSession(
        _Response(FW151_MAIN), _Response({"ESP_SW_version": raw_version})
    )
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: session)
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())

    async def _run() -> None:
        updater.data = await updater._async_update_data()
        # Must never raise — the docstring contract is "never fails setup".
        await updater.async_maybe_fetch_init_firmware()

    asyncio.run(_run())
    return updater._init_fw_fallback


def test_init_fallback_huge_int_does_not_raise_and_stays_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _fw_fallback_for(monkeypatch, 10**400) is None


def test_init_fallback_negative_int_stays_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _fw_fallback_for(monkeypatch, -151) is None


def test_init_fallback_normal_int_still_formats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _fw_fallback_for(monkeypatch, 151) == "1.51"


# =========================================================================
# D2 — reconfigure/reauth must check the reload result instead of claiming
# success via a fire-and-forget scheduled reload (config_flow.py)
# =========================================================================


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


# =========================================================================
# D3 — device trigger must fall back to entry.data["device_number"] when
# runtime_data is unavailable (device_trigger.py, entry mid-setup-retry)
# =========================================================================


async def _attached_event_data(entry: SimpleNamespace) -> dict:
    from unittest.mock import patch

    from custom_components.eveus import device_trigger

    device = SimpleNamespace(config_entries={"eid-1"})
    hass = Mock()
    hass.config_entries.async_get_entry = Mock(return_value=entry)
    registry = Mock()
    registry.async_get = Mock(return_value=device)
    config = {
        "platform": "device",
        "domain": device_trigger.DOMAIN,
        "device_id": "dev-1",
        "type": "charging_started",
    }
    with (
        patch.object(device_trigger.dr, "async_get", return_value=registry),
        # cv.template inside the real schema needs a hass context var that only
        # exists in a running HA instance; the schema itself is HA-owned code.
        patch.object(device_trigger.event_trigger, "TRIGGER_SCHEMA", new=lambda c: c),
        patch.object(
            device_trigger.event_trigger, "async_attach_trigger", new=AsyncMock()
        ) as attach,
    ):
        await device_trigger.async_attach_trigger(hass, config, Mock(), Mock())
    return attach.call_args.args[1]["event_data"]


from custom_components.eveus.const import DOMAIN  # noqa: E402


@pytest.mark.asyncio
async def test_trigger_falls_back_to_entry_data_device_number() -> None:
    entry = SimpleNamespace(
        domain=DOMAIN, runtime_data=None, data={"device_number": 2}
    )
    assert await _attached_event_data(entry) == {"device_number": 2}


@pytest.mark.asyncio
async def test_trigger_runtime_data_still_wins_over_entry_data() -> None:
    entry = SimpleNamespace(
        domain=DOMAIN,
        runtime_data=SimpleNamespace(device_number=3),
        data={"device_number": 2},
    )
    assert await _attached_event_data(entry) == {"device_number": 3}


@pytest.mark.asyncio
async def test_trigger_entry_data_fallback_rejects_bad_values() -> None:
    for bad in (True, "garbage", 0, None):
        entry = SimpleNamespace(
            domain=DOMAIN, runtime_data=None, data={"device_number": bad}
        )
        assert await _attached_event_data(entry) == {"device_number": 1}
