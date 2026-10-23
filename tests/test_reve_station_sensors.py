"""REVE_PLUS 1.0.3 station memory and start time as sensors.

For troubleshooting the firmware: created only for REVE, disabled by default,
diagnostic, and without ``state_class`` (no long-term statistics). Memory is
published in whole KiB, the start time only moves on a restart.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest

from custom_components.eveus import reve
from test_reve_firmware import _Charger, _classic_main, _reve_main, _updater
from test_reve_graphql_writes import _known

_SYSTEM = {
    "heapFreeSize": 19428, "heapMinFreeSize": 2716, "heapLargestFreeBlock": 12288,
    "heapTotalSize": 191704, "stationUptimeMs": 839781,
}
_KEYS = ("reve_free_memory", "reve_min_free_memory", "reve_largest_free_block", "reve_started")


def _wall(monkeypatch: pytest.MonkeyPatch, start: float = 1_761_249_600.0) -> list[float]:
    now = [start]
    monkeypatch.setattr("custom_components.eveus.reve.time.time", lambda: now[0])
    return now


def _sensors(updater: Any) -> tuple[list[Any], list[Any]]:
    from types import SimpleNamespace

    from conftest import disable_state_writes  # noqa: PLC0415
    from custom_components.eveus import EveusRuntimeData
    from custom_components.eveus.sensor import async_setup_entry

    updater._schedule_refresh = lambda: None
    added: list[Any] = []
    unloads: list[Any] = []
    entry = SimpleNamespace(
        entry_id="e1",
        data={"soc_mode": "basic"},
        runtime_data=EveusRuntimeData(
            updater=updater, device_number=1, title="Eveus Charger",
            soc_calculator=None, soc_limit=None, phases=1,
        ),
        async_on_unload=unloads.append,
    )
    asyncio.run(async_setup_entry(None, entry, lambda entities, **_kw: added.extend(entities)))
    station = [e for e in added if e.unique_id.removeprefix("eveus_") in _KEYS]
    for entity in station:
        disable_state_writes(entity)
        entity._maybe_finalize_device_info = lambda: None
    return station, unloads


def _by_key(station: list[Any]) -> dict[str, Any]:
    return {e.unique_id.removeprefix("eveus_"): e for e in station}


def test_started_holds_through_poll_jitter_and_moves_on_a_restart() -> None:
    started = reve.station_started(None, 10_000.0, 4_000_000)
    assert started == 6_000
    assert reve.station_started(started, 10_030.4, 4_029_000) == 6_000
    assert reve.station_started(started, 10_000.0 + reve.STARTED_TOLERANCE_SECONDS, 4_000_000) == 6_000
    assert reve.station_started(started, 10_061.0, 4_000_000) == 6_061
    assert reve.station_started(started, 20_000.0, 5_000) == 19_995


def test_the_sensors_show_memory_in_kib_and_the_start_time(monkeypatch: pytest.MonkeyPatch) -> None:
    _wall(monkeypatch)
    charger, updater = _known(monkeypatch)
    charger.station.update(_SYSTEM)
    asyncio.run(updater._async_update_data())
    station, _ = _sensors(updater)
    sensors = _by_key(station)
    assert set(sensors) == set(_KEYS)
    for entity in station:
        entity._handle_coordinator_update()
        assert entity.entity_registry_enabled_default is False
        assert entity.entity_category == "diagnostic"
        assert entity.state_class is None
    assert sensors["reve_free_memory"].native_value == 19
    assert sensors["reve_min_free_memory"].native_value == 3
    assert sensors["reve_largest_free_block"].native_value == 12
    assert sensors["reve_free_memory"].native_unit_of_measurement == "KiB"
    assert sensors["reve_started"].native_value == datetime.fromtimestamp(
        1_761_249_600 - 840, tz=timezone.utc  # 839.781 s rounds to 840
    )
    assert sensors["reve_started"].device_class == "timestamp"


def test_firmware_without_the_fields_leaves_them_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """REVE_PLUS 1.0.0 answers the new fields with nothing."""
    charger, updater = _known(monkeypatch)
    asyncio.run(updater._async_update_data())
    station, _ = _sensors(updater)
    for entity in station:
        entity._handle_coordinator_update()
        assert entity.available is False


def test_a_main_poll_drops_the_memory_but_keeps_the_start_time(monkeypatch: pytest.MonkeyPatch) -> None:
    _wall(monkeypatch)
    charger, updater = _known(monkeypatch)
    charger.station.update(_SYSTEM)
    asyncio.run(updater._async_update_data())
    updater._reve.from_main(_reve_main(), fallback=True)
    sensors = _by_key(_sensors(updater)[0])
    for entity in sensors.values():
        entity._handle_coordinator_update()
    assert sensors["reve_free_memory"].available is False
    assert sensors["reve_started"].available is True


def test_classic_chargers_get_no_station_sensors(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_classic_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    station, unloads = _sensors(updater)
    assert station == []
    assert len(unloads) == 1


def test_the_sensors_appear_when_the_charger_first_answers_as_reve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    from custom_components.eveus.sensor import async_setup_entry  # noqa: F401

    added: list[Any] = []
    from types import SimpleNamespace

    from custom_components.eveus import EveusRuntimeData
    from custom_components.eveus import sensor as sensor_platform

    updater._schedule_refresh = lambda: None
    entry = SimpleNamespace(
        entry_id="e1", data={"soc_mode": "basic"},
        runtime_data=EveusRuntimeData(
            updater=updater, device_number=1, title="Eveus Charger",
            soc_calculator=None, soc_limit=None, phases=1,
        ),
        async_on_unload=lambda _f: None,
    )
    asyncio.run(sensor_platform.async_setup_entry(None, entry, lambda e, **_kw: added.extend(e)))
    assert not {e.unique_id for e in added} & {f"eveus_{k}" for k in _KEYS}
    asyncio.run(updater._async_update_data())
    updater.async_update_listeners()
    updater.async_update_listeners()
    ids = [e.unique_id for e in added]
    assert all(ids.count(f"eveus_{k}") == 1 for k in _KEYS)
