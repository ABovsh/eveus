"""Hardening tests for the 4.10.1 deep-audit round (rc11).

Covers defects found by the parallel deep audit that prior rounds missed:

  * C-F01 — a host containing ASCII control characters is rejected instead of
    being silently normalized by ``urlparse`` to a different target.
  * C-F02 / C-F03 — adding (or reconfiguring to) an already-configured charger
    aborts with ``already_configured`` instead of a generic ``unknown`` error
    (``AbortFlow`` is no longer swallowed by the broad ``except Exception``).
  * C-F07 — a corrupt stored ``soc_mode`` no longer blocks reauthentication.
  * A-F06 — the command rate-limit wait is clamped so a backward wall-clock step
    cannot stall every command while holding the command lock.
  * B-F01 / B-F02 — a negative ``sessionTime`` reads ``unknown`` rather than a
    plausible ``0m`` and no longer leaks a negative ``duration_seconds``.
  * B-F03..B-F06 / B-F12 — the cumulative energy/cost statistics sensors reject
    corrupt finite outliers (e.g. ``1e100``) instead of recording them forever.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
import voluptuous as vol
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import AbortFlow

from conftest import EveusTestUpdater, TEST_HOST, TEST_PASSWORD, TEST_USERNAME
from custom_components.eveus import common_command, config_flow
from custom_components.eveus.config_flow import normalize_user_input
from custom_components.eveus.const import (
    CONF_MODEL,
    CONF_SOC_MODE,
    MODEL_16A,
    SOC_MODE_BASIC,
    SOC_MODE_OPTIONS,
)
from custom_components.eveus import sensor_definitions as sd


def _input(**overrides: object) -> dict[str, object]:
    data = {
        CONF_HOST: TEST_HOST,
        CONF_USERNAME: TEST_USERNAME,
        CONF_PASSWORD: TEST_PASSWORD,
        CONF_MODEL: MODEL_16A,
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# C-F01 — control characters in host are rejected, not silently stripped
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["a\nb.com", "http://a\rb.com", "1.2.\t3.4", "host\x7f.local"])
def test_split_host_rejects_control_characters(raw: str) -> None:
    with pytest.raises(vol.Invalid):
        config_flow._split_host_and_scheme(raw)


# ---------------------------------------------------------------------------
# C-F02 — duplicate host in the user flow propagates AbortFlow
# ---------------------------------------------------------------------------

def test_user_flow_propagates_already_configured_abort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_validate_input(hass, data):
        return {
            "title": f"Eveus Charger ({TEST_HOST})",
            "data": normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    def _abort() -> None:
        raise AbortFlow("already_configured")

    flow = config_flow.ConfigFlow()
    flow.hass = object()
    flow.async_set_unique_id = lambda unique_id: asyncio.sleep(0)
    flow._abort_if_unique_id_configured = _abort
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    with pytest.raises(AbortFlow):
        asyncio.run(flow.async_step_user(_input(**{CONF_SOC_MODE: SOC_MODE_BASIC})))


# ---------------------------------------------------------------------------
# C-F03 — duplicate host in reconfigure propagates AbortFlow
# ---------------------------------------------------------------------------

def test_reconfigure_flow_propagates_already_configured_abort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_validate_input(hass, data):
        return {
            "title": f"Eveus Charger ({TEST_HOST})",
            "data": normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    def _abort() -> None:
        raise AbortFlow("already_configured")

    flow = config_flow.ConfigFlow()
    flow.hass = object()
    flow._get_reconfigure_entry = lambda: SimpleNamespace(
        unique_id="different-old-host", data=_input()
    )
    flow.async_set_unique_id = lambda unique_id: asyncio.sleep(0)
    flow._abort_if_unique_id_configured = _abort
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    with pytest.raises(AbortFlow):
        asyncio.run(flow.async_step_reconfigure(_input()))


# ---------------------------------------------------------------------------
# C-F07 — corrupt stored soc_mode is normalized before reauth validation
# ---------------------------------------------------------------------------

def test_reauth_normalizes_corrupt_stored_soc_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_validate_input(hass, data):
        captured.update(data)
        return {
            "title": f"Eveus Charger ({TEST_HOST})",
            "data": normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    entry = SimpleNamespace(
        unique_id=TEST_HOST,
        data=_input(**{CONF_SOC_MODE: "totally-bogus"}),
    )
    flow = config_flow.ConfigFlow()
    flow.hass = object()
    flow._get_reauth_entry = lambda: entry
    flow.async_set_unique_id = lambda unique_id: asyncio.sleep(0)
    flow.async_update_reload_and_abort = lambda *a, **k: {"type": "abort"}
    monkeypatch.setattr(config_flow, "validate_input", fake_validate_input)

    asyncio.run(
        flow.async_step_reauth_confirm(
            {CONF_USERNAME: TEST_USERNAME, CONF_PASSWORD: "new-pass"}
        )
    )

    # The bogus stored soc_mode must have been replaced with a valid option, so
    # validate_input (and thus the user) is never blocked by it.
    assert captured[CONF_SOC_MODE] in SOC_MODE_OPTIONS


# ---------------------------------------------------------------------------
# A-F06 — command rate-limit wait is clamped against backward clock steps
# ---------------------------------------------------------------------------

def test_command_rate_limit_wait_is_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(common_command.asyncio, "sleep", fake_sleep)

    manager = common_command.CommandManager(SimpleNamespace())

    async def fake_post(command, value, extra=None):
        return True

    manager._post_command = fake_post
    # Simulate the wall clock having jumped backward by an hour after the last
    # command: time_since_last becomes strongly negative.
    manager._last_command_time = time.time() + 3600

    assert asyncio.run(manager.send_command("currentSet", 16)) is True
    # Without the clamp this would be ~3601s; with it, never more than 1s.
    assert all(delay <= 1.0 for delay in sleeps)


# ---------------------------------------------------------------------------
# B-F01 / B-F02 — negative sessionTime reads unknown, no negative attribute
# ---------------------------------------------------------------------------

def test_negative_session_time_reads_unknown() -> None:
    updater = EveusTestUpdater(data={"sessionTime": -1})
    assert sd.get_session_time(updater, None) is None
    assert sd.get_session_time_attrs(updater, None) == {}


def test_valid_session_time_still_renders() -> None:
    updater = EveusTestUpdater(data={"sessionTime": 3661})
    assert sd.get_session_time(updater, None) == "1h 01m"
    assert sd.get_session_time_attrs(updater, None) == {"duration_seconds": 3661}


# ---------------------------------------------------------------------------
# B-F03..B-F06 / B-F12 — energy/cost statistics sensors reject finite outliers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "getter,key",
    [
        (sd.get_session_energy, "sessionEnergy"),
        (sd.get_total_energy, "totalEnergy"),
        (sd.get_counter_a_energy, "IEM1"),
        (sd.get_counter_b_energy, "IEM2"),
        (sd.get_counter_a_cost, "IEM1_money"),
        (sd.get_counter_b_cost, "IEM2_money"),
        (sd.get_session_cost, "sessionMoney"),
    ],
)
def test_energy_cost_getters_reject_finite_outliers(getter, key: str) -> None:
    assert getter(EveusTestUpdater(data={key: 1e100}), None) is None
    # A normal reading still passes through untouched.
    assert getter(EveusTestUpdater(data={key: 42.5}), None) == pytest.approx(42.5)
