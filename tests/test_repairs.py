"""Unit tests for Eveus repair flows."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
from custom_components.eveus import repairs
from custom_components.eveus.config_flow import (
    CannotConnect,
    InvalidAuth,
    InvalidDevice,
    InvalidInput,
    normalize_user_input,
)
from custom_components.eveus.const import CONF_MODEL, MODEL_16A


class _ConfigEntries:
    def __init__(self, entry: object | None, others: list[object] | None = None) -> None:
        self.entry = entry
        self.others = others or []
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

    def async_entries(self, domain: str) -> list[object]:
        entries = [self.entry] if self.entry is not None else []
        return [*entries, *self.others]


def _data(**overrides: object) -> dict[str, object]:
    data = {
        CONF_HOST: TEST_HOST,
        CONF_USERNAME: TEST_USERNAME,
        CONF_PASSWORD: TEST_PASSWORD,
        CONF_MODEL: MODEL_16A,
    }
    data.update(overrides)
    return data


def test_invalid_config_repair_flow_updates_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_validate_input(hass, data):
        return {
            "title": f"Eveus Charger ({TEST_HOST})",
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
    assert config_entries.updated[0]["unique_id"] == TEST_HOST
    assert config_entries.reloaded == ["entry-id"]
    assert deleted == [("eveus", "invalid_config_entry-id")]


def test_invalid_config_repair_flow_preserves_device_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-running the repair flow must not strip integration-owned keys."""

    async def fake_validate_input(hass, data):
        return {
            "title": f"Eveus Charger ({TEST_HOST})",
            "data": normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    entry = SimpleNamespace(
        entry_id="entry-id",
        data=_data(device_number=4),
    )
    config_entries = _ConfigEntries(entry)
    hass = SimpleNamespace(config_entries=config_entries)
    monkeypatch.setattr(repairs, "validate_input", fake_validate_input)
    monkeypatch.setattr(
        repairs.ir,
        "async_delete_issue",
        lambda hass, domain, issue_id: None,
    )

    flow = repairs.InvalidConfigRepairFlow(hass, "invalid_config_entry-id", "entry-id")
    asyncio.run(flow.async_step_confirm(_data(**{CONF_PASSWORD: "new"})))

    assert config_entries.updated[0]["data"]["device_number"] == 4


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


def test_async_create_fix_flow_handles_missing_entry_id() -> None:
    hass = SimpleNamespace(config_entries=_ConfigEntries(None))

    flow = asyncio.run(repairs.async_create_fix_flow(hass, "invalid_config", None))

    assert isinstance(flow, repairs.InvalidConfigRepairFlow)
    assert flow._entry_id is None


def test_repair_flow_init_delegates_to_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = SimpleNamespace(entry_id="entry-id", data=_data())
    hass = SimpleNamespace(config_entries=_ConfigEntries(entry))
    flow = repairs.InvalidConfigRepairFlow(hass, "invalid_config_entry-id", "entry-id")

    async def fake_confirm(user_input=None):
        return {"type": "form", "user_input": user_input}

    monkeypatch.setattr(flow, "async_step_confirm", fake_confirm)

    assert asyncio.run(flow.async_step_init({"host": TEST_HOST})) == {
        "type": "form",
        "user_input": {"host": TEST_HOST},
    }


@pytest.mark.parametrize(
    ("exc", "error"),
    [
        (InvalidAuth, "invalid_auth"),
        (InvalidInput, "invalid_input"),
        (InvalidDevice, "invalid_device"),
        (RuntimeError, "unknown"),
    ],
)
def test_invalid_config_repair_flow_maps_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
    exc: type[Exception],
    error: str,
) -> None:
    async def fake_validate_input(hass, data):
        raise exc("boom")

    entry = SimpleNamespace(entry_id="entry-id", data=_data())
    hass = SimpleNamespace(config_entries=_ConfigEntries(entry))
    monkeypatch.setattr(repairs, "validate_input", fake_validate_input)

    flow = repairs.InvalidConfigRepairFlow(hass, "invalid_config_entry-id", "entry-id")
    result = asyncio.run(flow.async_step_confirm(_data()))

    assert result["type"] == "form"
    assert result["errors"] == {"base": error}


def test_invalid_config_repair_flow_blocks_unique_id_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_validate_input(hass, data):
        return {
            "title": "Eveus Charger (other-host)",
            "data": normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    entry = SimpleNamespace(entry_id="entry-id", unique_id=TEST_HOST, data=_data())
    other = SimpleNamespace(entry_id="other-id", unique_id="other-host", data={})
    hass = SimpleNamespace(config_entries=_ConfigEntries(entry, [other]))
    monkeypatch.setattr(repairs, "validate_input", fake_validate_input)

    flow = repairs.InvalidConfigRepairFlow(hass, "invalid_config_entry-id", "entry-id")
    result = asyncio.run(flow.async_step_confirm(_data(**{CONF_HOST: "other-host"})))

    assert result["type"] == "form"
    assert result["errors"] == {"base": "already_configured"}
    assert hass.config_entries.updated == []


def test_repair_keeps_issue_when_reload_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C-F09: a failed reload must not silently drop the repair notice.

    The issue is deleted only after a successful reload, so if reload raises the
    notice survives and the user can retry instead of losing the repair entry
    behind a generic "unknown" error.
    """

    async def fake_validate_input(hass, data):
        return {
            "title": f"Eveus Charger ({TEST_HOST})",
            "data": normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    class _ReloadFailingConfigEntries(_ConfigEntries):
        async def async_reload(self, entry_id: str) -> None:
            raise RuntimeError("reload exploded")

    deleted: list[tuple[str, str]] = []
    entry = SimpleNamespace(entry_id="entry-id", unique_id=TEST_HOST, data=_data())
    hass = SimpleNamespace(config_entries=_ReloadFailingConfigEntries(entry))
    monkeypatch.setattr(repairs, "validate_input", fake_validate_input)
    monkeypatch.setattr(
        repairs.ir,
        "async_delete_issue",
        lambda hass, domain, issue_id: deleted.append((domain, issue_id)),
    )

    flow = repairs.InvalidConfigRepairFlow(hass, "invalid_config_entry-id", "entry-id")
    result = asyncio.run(flow.async_step_confirm(_data(**{CONF_PASSWORD: "new"})))

    assert result["type"] == "form"
    assert result["errors"] == {"base": "unknown"}
    # The notice must still be present.
    assert deleted == []


def test_repair_keeps_issue_when_reload_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V-13: async_reload can return False without raising; the issue must stay."""

    async def fake_validate_input(hass, data):
        return {
            "title": f"Eveus Charger ({TEST_HOST})",
            "data": normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    class _ReloadFalseConfigEntries(_ConfigEntries):
        async def async_reload(self, entry_id: str) -> bool:
            return False

    deleted: list[tuple[str, str]] = []
    entry = SimpleNamespace(entry_id="entry-id", unique_id=TEST_HOST, data=_data())
    hass = SimpleNamespace(config_entries=_ReloadFalseConfigEntries(entry))
    monkeypatch.setattr(repairs, "validate_input", fake_validate_input)
    monkeypatch.setattr(
        repairs.ir,
        "async_delete_issue",
        lambda hass, domain, issue_id: deleted.append((domain, issue_id)),
    )

    flow = repairs.InvalidConfigRepairFlow(hass, "invalid_config_entry-id", "entry-id")
    result = asyncio.run(flow.async_step_confirm(_data(**{CONF_PASSWORD: "new"})))

    assert result["type"] == "form"
    assert result["errors"] == {"base": "unknown"}
    assert deleted == []


import asyncio as _asyncio
from custom_components.eveus import async_unload_entry


def _unload_hass_obj(unload_result: bool):
    async def _unload_platforms(_entry, _platforms) -> bool:
        return unload_result

    return SimpleNamespace(
        config_entries=SimpleNamespace(async_unload_platforms=_unload_platforms)
    )


class _UnloadIssueRecorder:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.deleted: list[str] = []

    def async_create_issue(self, _hass, _domain, issue_id, **_kwargs) -> None:
        self.created.append(issue_id)

    def async_delete_issue(self, _hass, _domain, issue_id) -> None:
        self.deleted.append(issue_id)


import pytest


@pytest.mark.parametrize("unload_result", [True, False])
def test_unload_deletes_issues_only_on_success(monkeypatch, unload_result) -> None:
    from custom_components.eveus import ir

    recorder = _UnloadIssueRecorder()
    monkeypatch.setattr(ir, "async_delete_issue", recorder.async_delete_issue)

    entry = SimpleNamespace(entry_id="e1")
    result = _asyncio.run(async_unload_entry(_unload_hass_obj(unload_result), entry))

    assert result is unload_result
    assert bool(recorder.deleted) is unload_result


def test_repair_flow_migrates_device_identifiers(monkeypatch) -> None:
    from custom_components.eveus import repairs, config_flow

    async def fake_validate_input(hass, data):
        return {
            "title": "Eveus Charger (newhost.local)",
            "data": config_flow.normalize_user_input(data),
            "device_info": {"current_set": 16},
        }

    migrated: list[tuple[str, str]] = []
    monkeypatch.setattr(repairs, "validate_input", fake_validate_input)
    monkeypatch.setattr(
        repairs,
        "migrate_device_identifiers",
        lambda hass, entry, old, new: migrated.append((old, new)),
    )
    monkeypatch.setattr(
        repairs.ir, "async_delete_issue", lambda *a, **k: None
    )

    class _Entries:
        updated: list = []

        def async_get_entry(self, entry_id):
            return entry

        def async_update_entry(self, entry, **kw):
            self.updated.append(kw)

        async def async_reload(self, entry_id):
            return None

        def async_entries(self, domain):
            return [entry]

    entry = SimpleNamespace(entry_id="e1", unique_id=TEST_HOST, data={
        "host": TEST_HOST,
        "username": TEST_USERNAME,
        "password": TEST_PASSWORD,
        "model": MODEL_16A,
    })
    hass = SimpleNamespace(config_entries=_Entries())

    flow = repairs.InvalidConfigRepairFlow(hass, "invalid_config_e1", "e1")
    _asyncio.run(
        flow.async_step_confirm(
            {
                "host": "newhost.local",
                "username": TEST_USERNAME,
                "password": TEST_PASSWORD,
                "model": MODEL_16A,
            }
        )
    )
    assert migrated == [(TEST_HOST, "newhost.local")]


import json
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "path",
    [
        "custom_components/eveus/strings.json",
        "custom_components/eveus/translations/en.json",
        "custom_components/eveus/translations/uk.json",
    ],
)
def test_repair_fix_flow_has_already_configured_error(path: str) -> None:
    data = json.loads((_ROOT / path).read_text())
    errors = data["issues"]["invalid_config"]["fix_flow"]["error"]
    assert "already_configured" in errors


@pytest.mark.asyncio
async def test_repair_flow_blocks_unique_id_collision():
    from unittest.mock import AsyncMock, MagicMock, patch
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
