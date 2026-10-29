"""A poll that fails in an unexpected way is still a failed poll.

An error outside the poll's own error handling (an ``OverflowError`` from an
absurd REVE number, 2026-10-30) reached Home Assistant directly: the coordinator
marked the poll failed, but the integration's failure accounting never ran, so
the charger still read as available, the outage clock never started and every
entity kept its last reading for as long as the error lasted.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.eveus import common_network
from custom_components.eveus.common_network import EveusUpdater
from test_reve_firmware import TEST_HOST, TEST_PASSWORD, TEST_USERNAME, _Charger, _classic_main, _Hass


def _updater(monkeypatch: pytest.MonkeyPatch) -> EveusUpdater:
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: _Charger(_classic_main()))
    monkeypatch.setattr(common_network, "async_dispatcher_send", lambda *args: None)
    entry = SimpleNamespace(entry_id="entry-1", async_on_unload=lambda callback: None)
    return EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass(), config_entry=entry)


@pytest.mark.parametrize("error", [OverflowError, TypeError, KeyError])
def test_an_unexpected_parse_error_is_accounted_as_a_failed_poll(monkeypatch, error) -> None:
    updater = _updater(monkeypatch)
    asyncio.run(updater._async_update_data())
    assert updater.available

    def broken(*_args, **_kwargs):
        raise error("boom")

    monkeypatch.setattr(common_network.EveusSnapshot, "parse", broken)
    with pytest.raises(UpdateFailed, match=error.__name__):
        asyncio.run(updater._async_update_data())

    assert not updater.available
    assert updater.seconds_unavailable >= 0 and updater._first_failure_monotonic is not None
    # A gap in the polls: a transition across it must not fire an event.
    assert updater._event_prev_state is None
