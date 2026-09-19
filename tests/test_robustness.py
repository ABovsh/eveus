"""Hostile-input robustness: shutdown races, nested JSON, huge integers,
the /init firmware fallback, and device-trigger number fallback."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from custom_components.eveus import _payload
from custom_components.eveus.common_command import CommandManager
from types import SimpleNamespace
from unittest.mock import Mock
from custom_components.eveus import config_flow
from custom_components.eveus.common_network import EveusUpdater
from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
from custom_components.eveus import common_network
from test_init_firmware_fallback import _MultiSession, _Response, FW151_MAIN
from custom_components.eveus.const import DOMAIN  # noqa: E402
from custom_components.eveus import device_trigger


def test_command_not_sent_once_shutdown_has_begun() -> None:
    updater = MagicMock()
    updater._shutting_down = True
    session = MagicMock()
    updater.get_session.return_value = session

    cm = CommandManager(updater)
    result = asyncio.run(cm.send_command("evseEnabled", 1))

    assert result is False
    session.post.assert_not_called()


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

def test_validate_device_response_tolerates_huge_currentset() -> None:
    payload = {"state": 2, "currentSet": 10**400}
    info = config_flow.validate_device_response(payload, "16A")
    assert info["current_set"] is None

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

def test_device_number_for_survives_non_finite_stored_value() -> None:
    """A non-finite device_number in entry.data must not crash trigger resolution.

    int() raises OverflowError on non-finite floats (e.g. a corrupted/hand-edited
    Store entry holding Infinity). __init__.py's identical coercion of the same
    field already guards OverflowError; this fallback must match it instead of
    crashing async_attach_trigger for every trigger card on the device.
    """
    entry = SimpleNamespace(
        domain=DOMAIN,
        runtime_data=None,
        data={"device_number": float("inf")},
    )
    device = SimpleNamespace(config_entries={"eid-1"})
    hass = Mock()
    hass.config_entries.async_get_entry = Mock(return_value=entry)
    registry = Mock()
    registry.async_get = Mock(return_value=device)

    with patch.object(device_trigger.dr, "async_get", return_value=registry):
        result = device_trigger._device_number_for(hass, "dev-1")

    assert result == 1
