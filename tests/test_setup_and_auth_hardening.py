"""Setup, authentication, and statistics hardening tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
import voluptuous as vol
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.exceptions import ConfigEntryAuthFailed

from conftest import EveusTestUpdater, TEST_HOST, spec_value_fn
from custom_components.eveus.common_command import CommandManager
from custom_components.eveus.config_flow import (
    InvalidDevice,
    _split_host_and_scheme,
    validate_device_response,
)
from custom_components.eveus.sensor_definitions import (
    get_counter_a_cost,
    get_counter_b_cost,
    get_primary_rate_cost,
    get_rate2_cost,
    get_rate3_cost,
    get_session_cost,
    get_sensor_specifications,
)


# F01 — path/query/fragment rejected
@pytest.mark.parametrize("raw", [
    "https://host/main?x=1",
    "host#frag",
    "host?a=b",
])
def test_split_host_rejects_query_or_fragment(raw):
    with pytest.raises(vol.Invalid, match="query or fragment"):
        _split_host_and_scheme(raw)


def test_split_host_accepts_bare_and_trailing_slash():
    assert _split_host_and_scheme("host")[0] == "host"
    assert _split_host_and_scheme("https://host/")[0] == "host"


# F02 — port 0 rejected
def test_split_host_rejects_port_zero():
    with pytest.raises(vol.Invalid, match="Invalid port"):
        _split_host_and_scheme("host:0")


# F04 — NaN currentSet rejected
def test_validate_device_response_rejects_nan():
    with pytest.raises(InvalidDevice, match="invalid current value"):
        validate_device_response({"state": 2, "currentSet": float("nan")}, "16A")
    with pytest.raises(InvalidDevice, match="invalid current value"):
        validate_device_response({"state": 2, "currentSet": float("inf")}, "16A")


# F05 — counter cost negative rejected
def test_counter_cost_rejects_negative():
    assert get_counter_a_cost(EveusTestUpdater({"IEM1_money": -1.0}), None) is None
    assert get_counter_b_cost(EveusTestUpdater({"IEM2_money": -0.01}), None) is None
    assert get_counter_a_cost(EveusTestUpdater({"IEM1_money": 12.5}), None) == 12.5


# F06 — tariff rate negative rejected (raw, before /100)
def test_tariff_rate_rejects_negative():
    assert get_primary_rate_cost(EveusTestUpdater({"tarif": -100}), None) is None
    assert get_rate2_cost(EveusTestUpdater({"tarifAValue": -50}), None) is None
    assert get_rate3_cost(EveusTestUpdater({"tarifBValue": -10}), None) is None
    assert get_primary_rate_cost(EveusTestUpdater({"tarif": 450}), None) == 4.5


# F07 — adaptive current/voltage negative rejected
def test_adaptive_metrics_reject_negative():
    assert spec_value_fn("adaptive_current_limit")(EveusTestUpdater({"aiModecurrent": -1}), None) is None
    assert spec_value_fn("adaptive_current_limit")(EveusTestUpdater({"aiModecurrent": 10}), None) == 10


# F08 — session_cost uses ₴ symbol as MEASUREMENT (per-session reset, no MONETARY)
def test_session_cost_spec_is_monetary_uah():
    by_key = {s.key: s for s in get_sensor_specifications(1)}
    spec = by_key["session_cost"]
    assert spec.device_class == SensorDeviceClass.MONETARY
    assert spec.unit == "UAH"
    assert spec.state_class == SensorStateClass.TOTAL


def test_session_cost_rejects_negative():
    assert get_session_cost(EveusTestUpdater({"sessionMoney": -0.5}), None) is None
    assert get_session_cost(EveusTestUpdater({"sessionMoney": 7.2}), None) == 7.2


# F12 — coordinator does not expose username/password as attrs
def test_coordinator_does_not_store_plaintext_credentials():
    from custom_components.eveus.common_network import EveusUpdater
    hass = MagicMock()
    hass.config = MagicMock()
    with patch.object(EveusUpdater, "__init__", lambda self, **kw: None):
        u = EveusUpdater()
    # Construct via the real __init__
    with patch("custom_components.eveus.common_network.DataUpdateCoordinator.__init__", return_value=None):
        u = EveusUpdater(host="h", username="user", password="pw", hass=hass)
    assert not hasattr(u, "username")
    assert not hasattr(u, "password")
    assert u._basic_auth == aiohttp.BasicAuth("user", "pw")


# F22 — repair flow refuses unique_id collision
@pytest.mark.asyncio
async def test_repair_flow_blocks_unique_id_collision():
    from custom_components.eveus.repairs import InvalidConfigRepairFlow

    other = MagicMock(entry_id="other", unique_id=TEST_HOST)
    target = MagicMock(entry_id="target", unique_id="192.168.1.10", data={
        "host": "192.168.1.10",
    })

    hass = MagicMock()
    hass.config_entries.async_entries.return_value = [other, target]
    hass.config_entries.async_update_entry = MagicMock()
    hass.config_entries.async_reload = AsyncMock()

    flow = InvalidConfigRepairFlow(hass, "invalid_config_target", "target")
    flow._get_entry = MagicMock(return_value=target)

    info = {
        "title": f"Eveus Charger ({TEST_HOST})",
        "data": {"host": TEST_HOST},
    }
    with patch(
        "custom_components.eveus.repairs.validate_input",
        AsyncMock(return_value=info),
    ):
        result = await flow.async_step_confirm({"host": TEST_HOST})

    assert result["type"] == "form"
    assert result["errors"] == {"base": "already_configured"}
    hass.config_entries.async_update_entry.assert_not_called()


# F23 — 401 from command propagates as ConfigEntryAuthFailed and does NOT retry
@pytest.mark.asyncio
async def test_command_401_raises_auth_failed_no_retry():
    updater = MagicMock()
    mgr = CommandManager(updater)
    response_err = aiohttp.ClientResponseError(
        request_info=MagicMock(),
        history=(),
        status=401,
        message="Unauthorized",
    )

    with patch.object(
        mgr,
        "_post_command",
        AsyncMock(side_effect=response_err),
    ) as post:
        with pytest.raises(ConfigEntryAuthFailed):
            await mgr.send_command("evseEnabled", 1)

    assert post.call_count == 1  # no retry on 401
