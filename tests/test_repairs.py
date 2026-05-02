"""Unit tests for Eveus repair flows."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from custom_components.eveus import repairs
from custom_components.eveus.config_flow import CannotConnect, normalize_user_input
from custom_components.eveus.const import CONF_MODEL, MODEL_16A


class _ConfigEntries:
    def __init__(self, entry: object | None) -> None:
        self.entry = entry
        self.updated: list[dict[str, object]] = []
        self.reloaded: list[str] = []

    def async_get_entry(self, entry_id: str) -> object | None:
        if self.entry and self.entry.entry_id == entry_id:
            return self.entry
        return None

    def async_update_entry(self, entry: object, **kwargs: object) -> None:
        self.updated.append(kwargs)

    async def async_reload(self, entry_id: str) -> None:
        self.reloaded.append(entry_id)


def _data(**overrides: object) -> dict[str, object]:
    data = {
        CONF_HOST: "192.168.1.50",
        CONF_USERNAME: "admin",
        CONF_PASSWORD: "secret",
        CONF_MODEL: MODEL_16A,
    }
    data.update(overrides)
    return data


def test_invalid_config_repair_flow_updates_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_validate_input(hass, data):
        return {
            "title": "Eveus Charger (192.168.1.50)",
            "data": normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    deleted: list[tuple[str, str]] = []
    entry = SimpleNamespace(entry_id="entry-id", data=_data())
    config_entries = _ConfigEntries(entry)
    hass = SimpleNamespace(config_entries=config_entries)
    monkeypatch.setattr(repairs, "validate_input", fake_validate_input)
    monkeypatch.setattr(
        repairs.ir,
        "async_delete_issue",
        lambda hass, domain, issue_id: deleted.append((domain, issue_id)),
    )

    flow = repairs.InvalidConfigRepairFlow(hass, "invalid_config_entry-id", "entry-id")
    result = asyncio.run(flow.async_step_confirm(_data(**{CONF_PASSWORD: "new"})))

    assert result["type"] == "create_entry"
    assert config_entries.updated[0]["data"][CONF_PASSWORD] == "new"
    assert config_entries.updated[0]["unique_id"] == "192.168.1.50"
    assert config_entries.reloaded == ["entry-id"]
    assert deleted == [("eveus", "invalid_config_entry-id")]


def test_invalid_config_repair_flow_returns_form_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_validate_input(hass, data):
        raise CannotConnect()

    entry = SimpleNamespace(entry_id="entry-id", data=_data())
    hass = SimpleNamespace(config_entries=_ConfigEntries(entry))
    monkeypatch.setattr(repairs, "validate_input", fake_validate_input)

    flow = repairs.InvalidConfigRepairFlow(hass, "invalid_config_entry-id", "entry-id")
    result = asyncio.run(flow.async_step_confirm(_data()))

    assert result["type"] == "form"
    assert result["errors"] == {"base": "cannot_connect"}


def test_invalid_config_repair_flow_aborts_when_entry_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted: list[tuple[str, str]] = []
    hass = SimpleNamespace(config_entries=_ConfigEntries(None))
    monkeypatch.setattr(
        repairs.ir,
        "async_delete_issue",
        lambda hass, domain, issue_id: deleted.append((domain, issue_id)),
    )

    flow = repairs.InvalidConfigRepairFlow(hass, "invalid_config_entry-id", "entry-id")
    result = asyncio.run(flow.async_step_confirm())

    assert result["type"] == "abort"
    assert result["reason"] == "entry_missing"
    assert deleted == [("eveus", "invalid_config_entry-id")]


def test_async_create_fix_flow_passes_entry_id() -> None:
    hass = SimpleNamespace(config_entries=_ConfigEntries(None))

    flow = asyncio.run(
        repairs.async_create_fix_flow(
            hass,
            "invalid_config_entry-id",
            {"entry_id": "entry-id"},
        )
    )

    assert isinstance(flow, repairs.InvalidConfigRepairFlow)
