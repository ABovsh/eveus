"""Hardening regression tests for the 2026-07-30 audit round."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import Mock

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.exceptions import ConfigEntryAuthFailed

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
from custom_components.eveus import common_network, config_flow
from custom_components.eveus.common_network import EveusUpdater
from custom_components.eveus.config_flow import normalize_user_input
from custom_components.eveus.const import (
    CONF_MODEL,
    DEVICE_STATE_CHARGING,
    DEVICE_STATE_STANDBY,
    EVENT_CHARGING_FINISHED,
    MODEL_16A,
)


# --- A-F01: a 401 poll must clear the transition memory like any other failure --


class _Hass:
    loop = None

    def __init__(self) -> None:
        self.bus = Mock()
        self.bus.async_fire = Mock()


class _Response:
    def __init__(self, status: int) -> None:
        self.status = status

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def text(self) -> str:
        return json.dumps({"state": 2})

    @property
    def content_length(self) -> int | None:
        return len(json.dumps({"state": 2}).encode())

    @property
    def headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}


class _Session:
    def __init__(self, response: _Response) -> None:
        self._response = response

    def post(self, *args: object, **kwargs: object) -> _Response:
        return self._response


def _updater_after_401(monkeypatch: pytest.MonkeyPatch) -> EveusUpdater:
    """Seed the transition memory, then take a 401 on the next poll."""
    monkeypatch.setattr(
        common_network, "async_get_clientsession", lambda hass: _Session(_Response(401))
    )
    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._schedule_post_command_refresh = Mock()
    updater._event_prev_state = DEVICE_STATE_CHARGING
    updater._event_prev_payload = {"state": DEVICE_STATE_CHARGING, "sessionEnergy": 12.5}
    updater._event_prev_error_code = 7
    updater._legacy_charging_latched = True
    updater._legacy_zero_power_polls = 1

    with pytest.raises(ConfigEntryAuthFailed):
        asyncio.run(updater._async_update_data())
    return updater


def test_auth_failure_clears_transition_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 401 is an offline gap for event purposes, exactly like a timeout."""
    updater = _updater_after_401(monkeypatch)

    assert updater._event_prev_state is None
    assert updater._event_prev_payload is None
    assert updater._event_prev_error_code is None
    assert updater._legacy_charging_latched is False
    assert updater._legacy_zero_power_polls == 0


def test_no_stale_charging_finished_after_auth_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-401 payload must not become a Last Session snapshot afterwards."""
    updater = _updater_after_401(monkeypatch)

    # Recovery poll (Force Refresh): the session ended during the auth gap, so
    # the transition is unobserved and must stay silent.
    updater._record_success(0.05, {"state": DEVICE_STATE_STANDBY})

    finished = [
        call.args[1]
        for call in updater.hass.bus.async_fire.call_args_list
        if call.args[0] == EVENT_CHARGING_FINISHED
    ]
    assert finished == []


# --- C-F01: reauth_confirm must let AbortFlow through, like user/reconfigure ---


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
