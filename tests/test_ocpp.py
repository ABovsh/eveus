"""Tests for OCPP control, status sensor, and the OCPP-enabled warning."""
from __future__ import annotations

import asyncio

import pytest

from conftest import EveusTestUpdater as _Updater
from conftest import disable_state_writes as _disable_state_writes
import custom_components.eveus as eveus_init
from custom_components.eveus import (
    _ocpp_issue_id,
    _update_ocpp_issue,
)
from custom_components.eveus.binary_sensor import EveusOcppConnectedBinarySensor
from custom_components.eveus.switch import BaseSwitchEntity, SWITCH_DESCRIPTIONS


def _ocpp_switch(updater: _Updater) -> BaseSwitchEntity:
    desc = next(d for d in SWITCH_DESCRIPTIONS if d.key == "ocpp")
    return BaseSwitchEntity(updater, desc)


def test_ocpp_switch_on_sends_enabled_and_vendor() -> None:
    updater = _Updater({"ocppEnabled": 0, "ocppVendor": 0})
    entity = _ocpp_switch(updater)
    _disable_state_writes(entity)

    asyncio.run(entity.async_turn_on())

    # The firmware only honors the toggle when ocppVendor rides along in the
    # same request, so the switch bundles both fields.
    assert updater.commands == [("ocppEnabled", 1)]
    assert updater.command_extras == [{"ocppVendor": 1}]
    assert entity.is_on is True


def test_ocpp_switch_off_sends_zero_vendor() -> None:
    updater = _Updater({"ocppEnabled": 1, "ocppVendor": 1})
    entity = _ocpp_switch(updater)
    _disable_state_writes(entity)

    asyncio.run(entity.async_turn_off())

    assert updater.commands == [("ocppEnabled", 0)]
    assert updater.command_extras == [{"ocppVendor": 0}]
    assert entity.is_on is False


def test_ocpp_connected_binary_sensor_reflects_field() -> None:
    updater = _Updater({"ocppconnected": 1})
    entity = EveusOcppConnectedBinarySensor(updater, 1)
    assert entity.is_on is True

    updater.data = {"ocppconnected": 0}
    assert entity.is_on is False


def test_ocpp_connected_binary_sensor_unknown_when_unavailable() -> None:
    updater = _Updater({"ocppconnected": 1}, available=False)
    entity = EveusOcppConnectedBinarySensor(updater, 1)
    entity._entity_available = False
    assert entity.is_on is None


def test_ocpp_issue_created_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, object]] = []
    deleted: list[str] = []
    monkeypatch.setattr(
        eveus_init.ir,
        "async_create_issue",
        lambda hass, domain, issue_id, **kw: created.append({"issue_id": issue_id, **kw}),
    )
    monkeypatch.setattr(
        eveus_init.ir,
        "async_delete_issue",
        lambda hass, domain, issue_id: deleted.append(issue_id),
    )

    entry = type("E", (), {"entry_id": "abc"})()
    updater = _Updater({"ocppEnabled": 1})

    _update_ocpp_issue(object(), entry, updater)

    assert created and created[0]["issue_id"] == _ocpp_issue_id(entry)
    assert created[0]["translation_key"] == "ocpp_enabled"
    assert created[0]["is_fixable"] is False
    assert not deleted


def test_ocpp_issue_cleared_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[str] = []
    deleted: list[str] = []
    monkeypatch.setattr(
        eveus_init.ir,
        "async_create_issue",
        lambda hass, domain, issue_id, **kw: created.append(issue_id),
    )
    monkeypatch.setattr(
        eveus_init.ir,
        "async_delete_issue",
        lambda hass, domain, issue_id: deleted.append(issue_id),
    )

    entry = type("E", (), {"entry_id": "abc"})()

    # An explicit 0 clears the warning.
    _update_ocpp_issue(object(), entry, _Updater({"ocppEnabled": 0}))
    assert deleted == [_ocpp_issue_id(entry)]

    # A missing/None/out-of-domain field must NOT clear it — that means the
    # firmware dropped/garbled the field, not that the user disabled OCPP.
    deleted.clear()
    for payload in ({}, {"ocppEnabled": None}, {"ocppEnabled": "bad"}):
        _update_ocpp_issue(object(), entry, _Updater(payload))

    assert not created
    assert not deleted
