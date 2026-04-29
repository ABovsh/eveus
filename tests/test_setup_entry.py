"""Unit tests for integration setup helpers."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

import custom_components.eveus as eveus
from custom_components.eveus.const import CONF_MODEL, MODEL_16A
from custom_components.eveus.sensor import async_setup_entry as async_setup_sensor_entry


class _ConfigEntries:
    def __init__(self) -> None:
        self.updated: list[dict[str, object]] = []
        self.forwarded: list[tuple[object, object]] = []
        self.unloaded: list[tuple[object, object]] = []
        self.reloaded: list[str] = []

    def async_entries(self, domain: str) -> list[object]:
        return []

    def async_update_entry(self, entry: object, **kwargs: object) -> None:
        self.updated.append(kwargs)

    async def async_forward_entry_setups(self, entry: object, platforms: object) -> None:
        self.forwarded.append((entry, platforms))

    async def async_unload_platforms(self, entry: object, platforms: object) -> bool:
        self.unloaded.append((entry, platforms))
        return True

    async def async_reload(self, entry_id: str) -> None:
        self.reloaded.append(entry_id)


class _Entry:
    def __init__(self, data: dict[str, object]) -> None:
        self.data = data
        self.title = "Eveus Charger"
        self.entry_id = "entry-id"
        self.runtime_data = None
        self.unloads: list[object] = []

    def async_on_unload(self, callback: object) -> None:
        self.unloads.append(callback)

    def add_update_listener(self, listener: object) -> object:
        return listener


class _Updater:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs
        self.host = kwargs["host"]
        self.available = True
        self.last_update_success = True
        self.data = {"currentSet": "16"}

    def async_add_listener(self, *args: object, **kwargs: object):
        return lambda: None

    async def async_config_entry_first_refresh(self) -> None:
        return None


class _AuthFailingUpdater(_Updater):
    async def async_config_entry_first_refresh(self) -> None:
        raise ConfigEntryAuthFailed("bad credentials")


class _UnexpectedFailingUpdater(_Updater):
    async def async_config_entry_first_refresh(self) -> None:
        raise RuntimeError("network stack exploded")


def _hass() -> SimpleNamespace:
    return SimpleNamespace(config_entries=_ConfigEntries())


def _data(**overrides: object) -> dict[str, object]:
    data = {
        CONF_HOST: "192.168.1.50",
        CONF_USERNAME: "admin",
        CONF_PASSWORD: "secret",
        CONF_MODEL: MODEL_16A,
    }
    data.update(overrides)
    return data


def test_async_setup_entry_populates_runtime_data(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = _hass()
    entry = _Entry(_data())
    monkeypatch.setattr(eveus, "EveusUpdater", _Updater)

    assert asyncio.run(eveus.async_setup_entry(hass, entry)) is True

    assert entry.runtime_data.updater.host == "192.168.1.50"
    assert entry.runtime_data.device_number == 1
    assert entry.runtime_data.soc_calculator is not None
    assert hass.config_entries.updated == [{"data": {**_data(), "device_number": 1}}]
    assert hass.config_entries.forwarded


def test_async_setup_entry_propagates_auth_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _hass()
    entry = _Entry(_data())
    monkeypatch.setattr(eveus, "EveusUpdater", _AuthFailingUpdater)

    with pytest.raises(ConfigEntryAuthFailed):
        asyncio.run(eveus.async_setup_entry(hass, entry))

    assert hass.config_entries.forwarded == []


def test_async_setup_entry_wraps_unexpected_refresh_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _hass()
    entry = _Entry(_data())
    monkeypatch.setattr(eveus, "EveusUpdater", _UnexpectedFailingUpdater)

    with pytest.raises(ConfigEntryNotReady, match="Unexpected error"):
        asyncio.run(eveus.async_setup_entry(hass, entry))

    assert hass.config_entries.forwarded == []


@pytest.mark.parametrize(
    "overrides",
    [
        {CONF_HOST: ""},
        {CONF_USERNAME: ""},
        {CONF_PASSWORD: ""},
        {CONF_MODEL: "bad"},
    ],
)
def test_async_setup_entry_rejects_invalid_stored_data(overrides: dict[str, object]) -> None:
    with pytest.raises(ConfigEntryNotReady):
        asyncio.run(eveus.async_setup_entry(_hass(), _Entry(_data(**overrides))))


def test_update_listener_and_unload_entry() -> None:
    hass = _hass()
    entry = _Entry(_data())

    asyncio.run(eveus.update_listener(hass, entry))
    assert hass.config_entries.reloaded == ["entry-id"]

    assert asyncio.run(eveus.async_unload_entry(hass, entry)) is True
    assert hass.config_entries.unloaded


def test_unload_entry_returns_false_when_platform_unload_fails() -> None:
    class _FailingConfigEntries(_ConfigEntries):
        async def async_unload_platforms(self, entry: object, platforms: object) -> bool:
            raise RuntimeError("unload failed")

    hass = SimpleNamespace(config_entries=_FailingConfigEntries())
    entry = _Entry(_data())

    assert asyncio.run(eveus.async_unload_entry(hass, entry)) is False


def test_sensor_setup_creates_standard_and_ev_sensors() -> None:
    added: list[object] = []
    entry = _Entry(_data())
    entry.runtime_data = SimpleNamespace(
        updater=_Updater(host="192.168.1.50", username="admin", password="secret"),
        device_number=1,
        soc_calculator=object(),
    )

    asyncio.run(
        async_setup_sensor_entry(
            object(),
            entry,
            lambda entities, update_before_add=False: added.extend(entities),
        )
    )

    assert len(added) >= 20
