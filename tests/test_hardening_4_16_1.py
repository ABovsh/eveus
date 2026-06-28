"""Regression tests for the 4.16.1 hardening round.

Each test pins a specific defect fix:

* command not sent after shutdown begins (command-after-teardown race)
* deeply nested JSON is recorded as a failed poll (RecursionError handling)
* SOC switch re-enabled while suspended survives a reload
* no reloading update listener is registered (single reload on entry update)
* reconfigure/repair never offer or silently flip SOC mode
* the SOC limit re-arms for a session that begins entirely between polls
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components import eveus
from custom_components.eveus import _payload
from custom_components.eveus.common_base import BaseEveusEntity
from custom_components.eveus.common_command import CommandManager
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from custom_components.eveus.config_flow import build_user_data_schema, normalize_user_input
from custom_components.eveus.const import (
    CONF_MODEL,
    CONF_SOC_MODE,
    SOC_MODE_BASIC,
)
from custom_components.eveus.ev_sensors import CachedSOCCalculator
from custom_components.eveus.soc_limit import SocLimitController
from custom_components.eveus.switch import EveusSocLimitSwitch


# --- R2-01: a queued/retrying command must not POST after shutdown begins ---

def test_command_not_sent_once_shutdown_has_begun() -> None:
    updater = MagicMock()
    updater._shutting_down = True
    session = MagicMock()
    updater.get_session.return_value = session

    cm = CommandManager(updater)
    result = asyncio.run(cm.send_command("evseEnabled", 1))

    assert result is False
    session.post.assert_not_called()


# --- R2-02: deeply nested JSON -> PayloadError (ValueError) so the poll fails ---

def test_deeply_nested_json_is_recorded_as_failed_poll() -> None:
    # A deeply nested JSON document makes json.loads raise RecursionError. The
    # exact depth that triggers it is interpreter-dependent, so assert the
    # conversion branch directly: a RecursionError from decoding must surface as
    # PayloadError (a ValueError) so the coordinator records the poll as failed.
    async def fake_read(response, *, limit=_payload.MAX_RESPONSE_BODY_BYTES):
        return b"[1]"

    assert issubclass(_payload.PayloadError, ValueError)
    with patch.object(_payload, "read_body_capped", fake_read), patch.object(
        _payload.json, "loads", side_effect=RecursionError
    ):
        with pytest.raises(_payload.PayloadError):
            asyncio.run(_payload.read_json_capped(MagicMock()))


# --- R2-05: re-enabled-while-suspended SOC switch survives a reload ---

def test_soc_switch_reenabled_while_suspended_survives_reload() -> None:
    controller = MagicMock()
    updater = MagicMock()
    updater.config_entry = MagicMock()
    # Fresh poll the setup performed before adding platforms: still suspended.
    updater.data = {"suspendLimits": 1}

    sw = EveusSocLimitSwitch(updater, controller, device_number=1)
    sw.hass = MagicMock()
    sw.async_write_ha_state = MagicMock()
    last = MagicMock()
    last.state = "on"
    sw.async_get_last_state = AsyncMock(return_value=last)

    with patch.object(BaseEveusEntity, "async_added_to_hass", AsyncMock()):
        asyncio.run(sw.async_added_to_hass())

    assert sw.is_on is True
    # First post-reload poll, master still on: must NOT be read as a fresh edge.
    sw._updater.data = {"suspendLimits": 1}
    sw._handle_coordinator_update()
    assert sw.is_on is True


# --- R2-03: no reloading update listener (so an entry update reloads once) ---

def test_no_reloading_update_listener_is_registered() -> None:
    # The listener was removed; reconfigure/reauth reload via the helper, repair
    # and the options flow reload explicitly. Its absence prevents a double
    # unload/setup cycle on every entry update.
    assert not hasattr(eveus, "update_listener")


# --- R2-04: reconfigure/repair edit connection details only ---

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
    flow.async_update_reload_and_abort = lambda entry, **kw: captured.update(kw) or {
        "type": "abort",
    }
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


# --- EVA-AUD-2026-06-28-01: re-arm for a session begun entirely between polls ---

def _calc(target=80, initial=20, cap=50, corr=0):
    c = CachedSOCCalculator()
    c.set_value("initial_soc", initial)
    c.set_value("battery_capacity", cap)
    c.set_value("soc_correction", corr)
    c.set_value("target_soc", target)
    return c


def _updater(session_energy=30.0, session_time=3600):
    u = MagicMock()
    u.available = True
    u.last_update_success = True
    u.device_number = 1
    u.data = {
        "state": 4,
        "sessionEnergy": session_energy,
        "sessionTime": session_time,
        "evseEnabled": 0,
        "suspendLimits": 0,
    }
    u.send_command = AsyncMock(return_value=True)
    return u


def _controller(calc, updater):
    hass = MagicMock()
    events = []
    hass.async_create_task = lambda coro: asyncio.run(coro)
    hass.bus.async_fire = lambda etype, data=None: events.append((etype, data))
    return SocLimitController(hass, updater, calc), events


def test_soc_limit_rearms_for_session_begun_between_polls() -> None:
    calc = _calc()
    updater = _updater(session_energy=30.0, session_time=3600)
    ctrl, events = _controller(calc, updater)
    ctrl.set_enabled(True)

    ctrl.process()  # at target -> Stop sent, attempt pending
    updater.data = {**updater.data, "evseEnabled": 1}
    ctrl.process()  # charger confirms -> event fires once, _fired latched
    assert len(events) == 1
    assert updater.send_command.call_count == 1

    # Session B begins entirely between polls: active, counters reset, charging,
    # already back at target. The reset sessionTime proves the new session.
    updater.data = {
        "state": 4,
        "sessionEnergy": 30.0,
        "sessionTime": 5,
        "evseEnabled": 0,
        "suspendLimits": 0,
    }
    ctrl.process()  # must re-arm and issue a second Stop
    assert updater.send_command.call_count == 2

    updater.data = {**updater.data, "evseEnabled": 1}
    ctrl.process()  # second confirmation -> exactly one more event
    assert len(events) == 2
