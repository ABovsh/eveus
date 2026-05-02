"""Unit tests for integration setup helpers."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError, ConfigEntryNotReady

import custom_components.eveus as eveus
from custom_components.eveus.const import CONF_MODEL, MODEL_16A
from custom_components.eveus.number import async_setup_entry as async_setup_number_entry
from custom_components.eveus.sensor import async_setup_entry as async_setup_sensor_entry
from custom_components.eveus.switch import (
    EveusResetCounterASwitch,
    async_setup_entry as async_setup_switch_entry,
)


class _ConfigEntries:
    def __init__(self) -> None:
        self.updated: list[dict[str, object]] = []
        self.forwarded: list[tuple[object, object]] = []
        self.unloaded: list[tuple[object, object]] = []
        self.reloaded: list[str] = []

    def async_entries(self, domain: str) -> list[object]:
        return []

    def async_get_entry(self, entry_id: str) -> object | None:
        return None

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

    async def async_shutdown(self) -> None:
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


@pytest.fixture(autouse=True)
def _patch_issue_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eveus.ir, "async_create_issue", lambda *args, **kwargs: None)
    monkeypatch.setattr(eveus.ir, "async_delete_issue", lambda *args, **kwargs: None)


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
    assert entry.runtime_data.updater.async_shutdown in entry.unloads


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
    with pytest.raises(ConfigEntryError):
        asyncio.run(eveus.async_setup_entry(_hass(), _Entry(_data(**overrides))))


def test_async_setup_entry_creates_repair_for_invalid_stored_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[dict[str, object]] = []

    def create_issue(hass, domain, issue_id, **kwargs):
        created.append({"domain": domain, "issue_id": issue_id, **kwargs})

    monkeypatch.setattr(eveus.ir, "async_create_issue", create_issue)
    monkeypatch.setattr(eveus.ir, "async_delete_issue", lambda *args, **kwargs: None)

    with pytest.raises(ConfigEntryError):
        asyncio.run(
            eveus.async_setup_entry(
                _hass(),
                _Entry(_data(**{CONF_MODEL: "bad"})),
            )
        )

    assert created
    assert created[0]["translation_key"] == "invalid_config"
    assert created[0]["is_fixable"] is True


def test_async_setup_entry_normalizes_stored_device_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _hass()
    entry = _Entry(_data(device_number="2"))
    monkeypatch.setattr(eveus, "EveusUpdater", _Updater)

    assert asyncio.run(eveus.async_setup_entry(hass, entry)) is True

    assert entry.runtime_data.device_number == 2
    assert hass.config_entries.updated == [{"data": {**_data(), "device_number": 2}}]


def test_update_listener_and_unload_entry() -> None:
    hass = _hass()
    entry = _Entry(_data())

    asyncio.run(eveus.update_listener(hass, entry))
    assert hass.config_entries.reloaded == ["entry-id"]

    assert asyncio.run(eveus.async_unload_entry(hass, entry)) is True
    assert hass.config_entries.unloaded


def test_unload_entry_propagates_platform_unload_failure() -> None:
    class _FailingConfigEntries(_ConfigEntries):
        async def async_unload_platforms(self, entry: object, platforms: object) -> bool:
            raise RuntimeError("unload failed")

    hass = SimpleNamespace(config_entries=_FailingConfigEntries())
    entry = _Entry(_data())

    import pytest

    with pytest.raises(RuntimeError, match="unload failed"):
        asyncio.run(eveus.async_unload_entry(hass, entry))


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


def test_switch_setup_creates_control_entities() -> None:
    added: list[object] = []
    entry = _Entry(_data())
    entry.runtime_data = SimpleNamespace(
        updater=_Updater(host="192.168.1.50", username="admin", password="secret"),
        device_number=2,
    )

    asyncio.run(
        async_setup_switch_entry(
            object(),
            entry,
            lambda entities: added.extend(entities),
        )
    )

    assert [entity.name for entity in added] == [
        "Stop Charging",
        "One Charge",
        "Reset Counter A",
    ]
    assert {entity.unique_id for entity in added} == {
        "eveus2_stop_charging",
        "eveus2_one_charge",
        "eveus2_reset_counter_a",
    }


def test_number_setup_creates_current_entity() -> None:
    added: list[object] = []
    entry = _Entry(_data())
    entry.runtime_data = SimpleNamespace(
        updater=_Updater(host="192.168.1.50", username="admin", password="secret"),
        device_number=3,
    )

    asyncio.run(
        async_setup_number_entry(
            object(),
            entry,
            lambda entities: added.extend(entities),
        )
    )

    assert len(added) == 1
    assert added[0].name == "Charging Current"
    assert added[0].unique_id == "eveus3_charging_current"


def test_reset_counter_safe_mode_task_is_cancelled_on_remove(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callbacks: list[object] = []
    cancel_called = False

    def fake_async_call_later(hass, delay, action):
        def cancel() -> None:
            nonlocal cancel_called
            cancel_called = True
        return cancel

    async def noop_added_to_hass(self) -> None:
        return None

    monkeypatch.setattr(
        "custom_components.eveus.common_base.BaseEveusEntity.async_added_to_hass",
        noop_added_to_hass,
    )
    monkeypatch.setattr(
        "custom_components.eveus.switch.async_call_later",
        fake_async_call_later,
    )

    entity = EveusResetCounterASwitch(
        _Updater(host="192.168.1.50", username="admin", password="secret"),
        1,
    )
    entity.hass = object()
    entity.async_on_remove = callbacks.append

    asyncio.run(entity.async_added_to_hass())

    assert len(callbacks) == 1
    callbacks[0]()
    assert cancel_called is True
