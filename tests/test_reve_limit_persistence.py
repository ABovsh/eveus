"""Audit 2026-11-12 #02: a limit value set while the limit is off survives a
reload or restart.

REVE has no on/off flag for its session limits: writing a value switches the
limit on, so a value set while the limit is off is kept only in Home Assistant
until it is switched on. Kept only in memory, a reload lost it and switching
the limit on applied the charger's older ``/main`` value instead.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.eveus import common_network
from test_reve_firmware import _no_sleep
from test_reve_graphql_writes import _inputs, _known_charging, _send


class _SharedStore:
    """Home Assistant's ``Store``, one file per key shared by every instance."""

    files: dict[str, Any] = {}
    writes: list[tuple[str, Any]] = []

    def __init__(self, hass: Any, version: int, key: str) -> None:
        self.key = key

    async def async_load(self) -> Any:
        return _SharedStore.files.get(self.key)

    def async_delay_save(self, data_func: Any, delay: float = 0) -> None:
        assert delay > 0
        self._write(data_func())

    async def async_save(self, data: Any) -> None:
        self._write(data)

    def _write(self, data: Any) -> None:
        _SharedStore.files[self.key] = dict(data)
        _SharedStore.writes.append((self.key, dict(data)))


@pytest.fixture(autouse=True)
def _store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common_network, "Store", _SharedStore)
    monkeypatch.setattr(_SharedStore, "files", {})
    monkeypatch.setattr(_SharedStore, "writes", [])
    monkeypatch.setattr("custom_components.eveus.common_command.asyncio.sleep", _no_sleep)


def _entry_updater(monkeypatch: pytest.MonkeyPatch, charger: Any, entry_id: str = "e1") -> Any:
    updater = common_network.EveusUpdater(
        "192.168.1.50", "", "", charger_hass(), config_entry=SimpleNamespace(
            entry_id=entry_id, async_on_unload=lambda callback: None
        ),
    )
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: charger)
    return updater


def charger_hass() -> Any:
    from test_reve_firmware import _Hass

    return _Hass()


def _started(monkeypatch: pytest.MonkeyPatch, charger: Any, entry_id: str = "e1") -> Any:
    updater = _entry_updater(monkeypatch, charger, entry_id)
    asyncio.run(updater.async_load_reve_limits())
    asyncio.run(updater._async_update_data())
    charger.calls.clear()
    return updater


LIMITS = [
    ("energyLimit", "energyLimitS", "energyLimit", 1000, 20, 50),
    ("timeLimit", "timeLimitS", "timeLimit", 1, 3600, 7200),
    ("moneyLimit", "moneyLimitS", "costLimit", 1, 100, 200),
]


@pytest.mark.parametrize(("key", "flag", "field", "factor", "chosen", "old"), LIMITS)
def test_an_off_limit_value_survives_a_reload(monkeypatch, key, flag, field, factor, chosen, old) -> None:
    charger, _ = _known_charging(monkeypatch)
    charger.station["session"][field] = None
    charger.main[key] = old
    updater = _started(monkeypatch, charger)
    assert _send(updater, key, chosen * factor)
    asyncio.run(updater._async_update_data())
    assert updater.snapshot.get(key) == chosen
    asyncio.run(updater.async_shutdown())
    fresh = _started(monkeypatch, charger)
    assert fresh.snapshot.get(key) == chosen
    assert _send(fresh, flag, 1)
    assert _inputs(charger)[-1]["session"][field] == chosen * factor


@pytest.mark.parametrize(("key", "flag", "field", "factor", "chosen", "old"), LIMITS)
def test_an_off_limit_value_survives_an_abrupt_restart(monkeypatch, key, flag, field, factor, chosen, old) -> None:
    charger, _ = _known_charging(monkeypatch)
    charger.station["session"][field] = None
    charger.main[key] = old
    updater = _started(monkeypatch, charger)
    assert _send(updater, key, chosen * factor)
    fresh = _started(monkeypatch, charger)  # no shutdown flush
    assert _send(fresh, flag, 1)
    assert _inputs(charger)[-1]["session"][field] == chosen * factor


def test_an_active_limit_on_the_charger_overrides_a_saved_value(monkeypatch) -> None:
    charger, _ = _known_charging(monkeypatch)
    charger.station["session"]["energyLimit"] = 30000
    _SharedStore.files["eveus_reve_limits_e1"] = {"energyLimit": 20}
    updater = _started(monkeypatch, charger)
    assert updater.snapshot.get("energyLimit") == 30
    assert _SharedStore.files["eveus_reve_limits_e1"] == {"energyLimit": 30}


def test_unchanged_values_are_not_written_again(monkeypatch) -> None:
    charger, _ = _known_charging(monkeypatch)
    charger.station["session"]["energyLimit"] = 30000
    updater = _started(monkeypatch, charger)
    writes = len(_SharedStore.writes)
    for _ in range(3):
        asyncio.run(updater._async_update_data())
    asyncio.run(updater.async_shutdown())
    assert len(_SharedStore.writes) == writes


@pytest.mark.parametrize(
    "saved",
    [None, [], "x", {"energyLimit": "20"}, {"energyLimit": -1}, {"energyLimit": float("nan")},
     {"energyLimit": True}, {"unknown": 5}],
)
def test_what_storage_hands_back_is_checked(monkeypatch, saved) -> None:
    charger, _ = _known_charging(monkeypatch)
    charger.station["session"]["energyLimit"] = None
    charger.main["energyLimit"] = 50
    _SharedStore.files["eveus_reve_limits_e1"] = saved
    updater = _started(monkeypatch, charger)
    assert updater._reve._limit_values.get("unknown") is None
    assert _send(updater, "energyLimitS", 1)
    assert _inputs(charger)[-1]["session"]["energyLimit"] == 50000


def test_each_entry_keeps_its_own_values(monkeypatch) -> None:
    charger, _ = _known_charging(monkeypatch)
    charger.station["session"]["energyLimit"] = None
    first = _started(monkeypatch, charger, "e1")
    assert _send(first, "energyLimit", 20000)
    second = _started(monkeypatch, charger, "e2")
    assert second._reve._limit_values.get("energyLimit") != 20
    assert "eveus_reve_limits_e2" not in _SharedStore.files or _SharedStore.files["eveus_reve_limits_e2"].get("energyLimit") != 20


class _DeferredStore(_SharedStore):
    """Like Home Assistant's ``Store``: a delayed save waits for its timer, and
    an immediate save (or removal) on the same instance replaces it."""

    pending: dict[str, Any] = {}

    def async_delay_save(self, data_func: Any, delay: float = 0) -> None:
        assert delay > 0
        _DeferredStore.pending[self.key] = (self, data_func)

    async def async_save(self, data: Any) -> None:
        _DeferredStore.pending.pop(self.key, None)
        self._write(data)


def test_unload_replaces_a_pending_delayed_save(monkeypatch) -> None:
    """Removing an entry deletes its file through a new ``Store``, which cannot
    cancel the unloaded updater's pending delayed save: that save would land
    after the removal and leave the file behind for good."""
    monkeypatch.setattr(common_network, "Store", _DeferredStore)
    monkeypatch.setattr(_DeferredStore, "pending", {})
    charger, _ = _known_charging(monkeypatch)
    charger.station["session"]["energyLimit"] = None
    updater = _started(monkeypatch, charger)
    assert _send(updater, "energyLimit", 20000)
    assert "eveus_reve_limits_e1" in _DeferredStore.pending
    asyncio.run(updater.async_shutdown())
    assert "eveus_reve_limits_e1" not in _DeferredStore.pending
    assert _SharedStore.files["eveus_reve_limits_e1"]["energyLimit"] == 20


# ---------------------------------------------------------------------------
# Mutation round 4.31.0: the store's identity, failures, and repeat flushes.
# ---------------------------------------------------------------------------

def test_the_limit_store_belongs_to_this_home_assistant_at_its_version(monkeypatch) -> None:
    from custom_components.eveus.reve import REVE_LIMIT_STORE_VERSION

    made: list[tuple] = []

    class _Recorded(_SharedStore):
        def __init__(self, hass: Any, version: int, key: str) -> None:
            made.append((hass, version, key))
            super().__init__(hass, version, key)

    monkeypatch.setattr(common_network, "Store", _Recorded)
    charger, _ = _known_charging(monkeypatch)
    updater = _entry_updater(monkeypatch, charger)
    asyncio.run(updater.async_load_reve_limits())
    assert made == [(updater.hass, REVE_LIMIT_STORE_VERSION, "eveus_reve_limits_e1")]


def test_a_store_that_cannot_load_is_dropped_and_never_written(monkeypatch) -> None:
    class _Broken(_SharedStore):
        async def async_load(self) -> Any:
            raise OSError("disk")

    monkeypatch.setattr(common_network, "Store", _Broken)
    charger, _ = _known_charging(monkeypatch)
    charger.station["session"]["energyLimit"] = None
    updater = _started(monkeypatch, charger)
    assert updater._limit_store is None
    assert _send(updater, "energyLimit", 20000)
    asyncio.run(updater.async_shutdown())
    assert _SharedStore.writes == []


def test_loaded_values_the_charger_agrees_with_are_not_written_back(monkeypatch) -> None:
    charger, _ = _known_charging(monkeypatch)
    charger.station["session"]["energyLimit"] = None
    _SharedStore.files["eveus_reve_limits_e1"] = {"energyLimit": 20}
    updater = _started(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    updater._save_reve_limits()
    asyncio.run(updater.async_shutdown())
    assert _SharedStore.writes == []


def test_a_change_during_shutdown_is_left_to_the_flush(monkeypatch) -> None:
    charger, _ = _known_charging(monkeypatch)
    charger.station["session"]["energyLimit"] = None
    updater = _started(monkeypatch, charger)
    updater._shutting_down = True
    updater._reve._limit_values["energyLimit"] = 20
    updater._save_reve_limits()
    assert _SharedStore.writes == []


def test_a_second_flush_with_nothing_new_writes_nothing(monkeypatch) -> None:
    charger, _ = _known_charging(monkeypatch)
    charger.station["session"]["energyLimit"] = None
    updater = _started(monkeypatch, charger)
    updater._reve._limit_values["energyLimit"] = 20
    asyncio.run(updater._flush_reve_limits())
    asyncio.run(updater._flush_reve_limits())
    assert _SharedStore.writes == [("eveus_reve_limits_e1", {"energyLimit": 20})]
