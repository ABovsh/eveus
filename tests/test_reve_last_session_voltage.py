"""Last Session Min/Max Voltage: read from the REVE station's own session record.

REVE keeps the lowest and highest mains voltage of every finished session
(``listSessions``: ``minVoltageA``/``maxVoltageA``). Only REVE has it, only phase
A is real on a one-phase charger (B and C carry 999/0 placeholders), and the
station answers from its own memory, so a Home Assistant that was down when the
session ended still gets it.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import UnitOfElectricPotential

from custom_components.eveus import reve
from test_reve_firmware import _Charger, _classic_main, _updater
from test_reve_graphql_writes import _known

_ITEM = {
    "externalId": "6aee316278ab638163652852", "syncedAt": 1794132327,
    "startAt": 1793995106, "stopAt": 1794132326,
    "minVoltageA": 221, "maxVoltageA": 231,
    "minVoltageB": 999, "maxVoltageB": 0, "minVoltageC": 999, "maxVoltageC": 0,
}


def _station(charger: _Charger, *items: dict[str, Any]) -> None:
    charger.sessions = list(items)


def test_parse_takes_phase_a_of_the_newest_session() -> None:
    assert reve.parse_last_session_voltage([_ITEM]) == {
        "min": 221, "max": 231, "stop_at": 1794132326,
    }


@pytest.mark.parametrize("items", [None, [], {}, "x", [None], [{}], [{"stopAt": "x"}]])
def test_parse_gives_nothing_for_a_missing_or_malformed_session(items: Any) -> None:
    assert reve.parse_last_session_voltage(items) is None


@pytest.mark.parametrize(
    "low, high, want",
    [
        (999, 0, (None, None)),          # the placeholders of a phase that is not there
        (0, 0, (None, None)),
        (221, 0, (221, None)),
        (None, 231, (None, 231)),
        (True, 231, (None, 231)),        # a bool is not a voltage
        (221.0, 231.0, (221, 231)),
        (221, 501, (221, None)),         # above the plausible ceiling
        (-5, 231, (None, 231)),
        (231, 221, (None, None)),        # min above max: corrupt, trust neither
    ],
)
def test_parse_drops_what_cannot_be_a_voltage(low: Any, high: Any, want: tuple[Any, Any]) -> None:
    parsed = reve.parse_last_session_voltage(
        [{**_ITEM, "minVoltageA": low, "maxVoltageA": high}]
    )
    assert (parsed["min"], parsed["max"]) == want


def test_a_station_with_no_session_answers_once_and_is_not_asked_again(monkeypatch: pytest.MonkeyPatch) -> None:
    charger, updater = _known(monkeypatch)
    _station(charger)

    async def both() -> list[Any]:
        return await asyncio.gather(updater.async_read_last_session(), updater.async_read_last_session())

    assert asyncio.run(both()) == [reve.NO_SESSION] * 2
    assert sum(1 for path, _ in charger.calls if path == "/graphql") == 1


def test_the_updater_reads_the_last_session_from_the_station(monkeypatch: pytest.MonkeyPatch) -> None:
    charger, updater = _known(monkeypatch)
    _station(charger, _ITEM)
    got = asyncio.run(updater.async_read_last_session())
    assert got == {"min": 221, "max": 231, "stop_at": 1794132326}
    sent = [body for path, body in charger.calls if path == "/graphql"]
    assert len(sent) == 1
    assert sent[0]["operationName"] == "ListSessions"
    assert sent[0]["variables"] == {"limit": 1, "offset": 0}
    assert "listSessions" in sent[0]["query"]


def test_two_sensors_asking_together_cost_one_request(monkeypatch: pytest.MonkeyPatch) -> None:
    charger, updater = _known(monkeypatch)
    _station(charger, _ITEM)

    async def both() -> list[Any]:
        return await asyncio.gather(updater.async_read_last_session(), updater.async_read_last_session())

    assert asyncio.run(both()) == [{"min": 221, "max": 231, "stop_at": 1794132326}] * 2
    assert sum(1 for path, _ in charger.calls if path == "/graphql") == 1


def test_a_failed_read_gives_nothing_and_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    charger, updater = _known(monkeypatch)
    _station(charger, _ITEM)
    charger.graphql_status = 500
    assert asyncio.run(updater.async_read_last_session()) is None
    charger.graphql_status = 200
    assert asyncio.run(updater.async_read_last_session()) is not None


def test_classic_firmware_is_never_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_classic_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    charger.calls.clear()
    assert asyncio.run(updater.async_read_last_session()) is None
    assert not [1 for path, _ in charger.calls if path == "/graphql"]


# -- the sensors --------------------------------------------------------------

def _setup(updater: Any) -> tuple[dict[str, Any], list[Any]]:
    from conftest import disable_state_writes  # noqa: PLC0415
    from custom_components.eveus import EveusRuntimeData
    from custom_components.eveus.sensor import async_setup_entry

    updater._schedule_refresh = lambda: None
    added: list[Any] = []
    unloads: list[Any] = []
    entry = SimpleNamespace(
        entry_id="e1", data={"soc_mode": "basic"},
        runtime_data=EveusRuntimeData(
            updater=updater, device_number=1, title="Eveus Charger",
            soc_calculator=None, soc_limit=None, phases=1,
        ),
        async_on_unload=unloads.append,
    )
    asyncio.run(async_setup_entry(None, entry, lambda entities, **_kw: added.extend(entities)))
    found = {
        e.unique_id.removeprefix("eveus_"): e for e in added
        if "last_session_m" in e.unique_id and "voltage" in e.unique_id
    }
    for entity in found.values():
        disable_state_writes(entity)
        entity._maybe_finalize_device_info = lambda: None
    return found, unloads


def test_reve_gets_min_and_max_voltage_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _charger, updater = _known(monkeypatch)
    found, _ = _setup(updater)
    assert set(found) == {"last_session_min_voltage", "last_session_max_voltage"}
    for entity in found.values():
        assert entity.entity_registry_enabled_default is True
        assert entity.device_class == SensorDeviceClass.VOLTAGE
        assert entity.native_unit_of_measurement == UnitOfElectricPotential.VOLT
        # A once-per-session reading: no long-term statistics.
        assert entity.state_class is None


def test_classic_firmware_gets_neither(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_classic_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    found, _ = _setup(updater)
    assert found == {}


def test_the_sensors_take_their_value_from_the_station(monkeypatch: pytest.MonkeyPatch) -> None:
    charger, updater = _known(monkeypatch)
    _station(charger, _ITEM)
    found, _ = _setup(updater)
    for entity in found.values():
        assert asyncio.run(entity.async_refresh_from_station()) is True
    assert found["last_session_min_voltage"].native_value == 221
    assert found["last_session_max_voltage"].native_value == 231
    assert found["last_session_max_voltage"].extra_state_attributes["finished_at"].startswith("2026-")


def test_a_session_older_than_the_finish_is_not_taken(monkeypatch: pytest.MonkeyPatch) -> None:
    """The station may not have stored the session that just ended yet."""
    charger, updater = _known(monkeypatch)
    _station(charger, _ITEM)
    found, _ = _setup(updater)
    sensor = found["last_session_min_voltage"]
    assert asyncio.run(sensor.async_refresh_from_station(newer_than=_ITEM["stopAt"] + 600)) is False
    assert sensor.native_value is None
    assert asyncio.run(sensor.async_refresh_from_station(newer_than=_ITEM["stopAt"] - 60)) is True
    assert sensor.native_value == 221


def test_a_failed_read_keeps_the_last_value(monkeypatch: pytest.MonkeyPatch) -> None:
    charger, updater = _known(monkeypatch)
    _station(charger, _ITEM)
    found, _ = _setup(updater)
    sensor = found["last_session_min_voltage"]
    assert asyncio.run(sensor.async_refresh_from_station()) is True
    charger.graphql_status = 500
    updater._last_session_read = None  # past the moment in which reads share one answer
    assert asyncio.run(sensor.async_refresh_from_station()) is False
    assert sensor.native_value == 221


def test_a_station_without_a_session_leaves_the_sensor_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    charger, updater = _known(monkeypatch)
    _station(charger)
    found, _ = _setup(updater)
    sensor = found["last_session_min_voltage"]
    # A station with no session is a final answer at start, and not yet the one
    # that just ended after a finish.
    assert asyncio.run(sensor.async_refresh_from_station()) is True
    assert asyncio.run(sensor.async_refresh_from_station(newer_than=1.0)) is False
    assert sensor.native_value is None
    assert sensor.available is True
    assert reve.NO_SESSION["stop_at"] is None


# ---------------------------------------------------------------------------
# Audit 2026-11-12 #03/#04: reads run in the background, and a burst that
# ended on a failed read is owed: a later successful poll retries it, at most
# every few minutes, until the station answers.
# ---------------------------------------------------------------------------

def _owed_fixture(monkeypatch: pytest.MonkeyPatch):
    from custom_components.eveus import reve_sensors

    charger, updater = _known(monkeypatch)
    _station(charger, _ITEM)
    found, _ = _setup(updater)
    sensor = found["last_session_min_voltage"]

    async def no_wait(*_args: Any) -> None:
        return None

    monkeypatch.setattr(reve_sensors.asyncio, "sleep", no_wait)
    clock = [1000.0]
    monkeypatch.setattr(reve_sensors.time, "monotonic", lambda: clock[0])
    reads: list[None] = []
    original = updater.async_read_last_session

    async def counted() -> Any:
        reads.append(None)
        return await original()

    updater.async_read_last_session = counted
    tasks: list[asyncio.Task[Any]] = []

    def background(coro: Any, name: str, **_kw: Any) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coro)
        tasks.append(task)
        return task

    sensor.hass = SimpleNamespace(async_create_background_task=background)
    updater.last_update_success = True
    return charger, updater, sensor, clock, reads, tasks


async def _settle(tasks: list[asyncio.Task[Any]]) -> None:
    await asyncio.gather(*tasks)


def test_an_exhausted_read_is_retried_once_the_api_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    charger, updater, sensor, clock, reads, tasks = _owed_fixture(monkeypatch)

    async def scenario() -> None:
        charger.graphql_status = 500
        sensor._start_reads((0.0, 30.0, 60.0), None)
        await _settle(tasks)
        assert sensor.native_value is None and len(reads) == 3
        charger.graphql_status = 200
        await updater._async_update_data()
        sensor._handle_coordinator_update()
        await _settle(tasks)
        assert sensor.native_value == 221
        # Settled: later polls read nothing more.
        clock[0] += 3600
        sensor._handle_coordinator_update()
        await _settle(tasks)
        assert len(reads) == 4

    asyncio.run(scenario())


def test_an_owed_read_backs_off_while_the_api_stays_down(monkeypatch: pytest.MonkeyPatch) -> None:
    charger, _updater_, sensor, clock, reads, tasks = _owed_fixture(monkeypatch)

    async def scenario() -> None:
        charger.graphql_status = 500
        sensor._start_reads((0.0,), None)
        await _settle(tasks)
        sensor._handle_coordinator_update()
        await _settle(tasks)
        assert len(reads) == 2
        clock[0] += 60
        sensor._handle_coordinator_update()
        await _settle(tasks)
        assert len(reads) == 2, "an owed read must not be retried on every poll"
        clock[0] += 300
        sensor._handle_coordinator_update()
        await _settle(tasks)
        assert len(reads) == 3

    asyncio.run(scenario())


def test_an_owed_read_waits_for_a_successful_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    charger, updater, sensor, _clock, reads, tasks = _owed_fixture(monkeypatch)

    async def scenario() -> None:
        charger.graphql_status = 500
        sensor._start_reads((0.0,), None)
        await _settle(tasks)
        updater.last_update_success = False
        sensor._handle_coordinator_update()
        await _settle(tasks)
        assert len(reads) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(("shown", "unit"), [("0.221", "kV"), ("221000", "mV")])
def test_a_restored_voltage_shown_in_another_unit_is_read_back_in_volts(
    monkeypatch: pytest.MonkeyPatch, shown: str, unit: str
) -> None:
    from homeassistant.util.unit_conversion import ElectricPotentialConverter

    if unit not in ElectricPotentialConverter.VALID_UNITS:
        # Older Home Assistant (kV arrived after 2025.1) cannot show it either.
        pytest.skip(f"{unit} is not a voltage unit in this Home Assistant")
    _charger, updater = _known(monkeypatch)
    sensor = _setup(updater)[0]["last_session_min_voltage"]
    asyncio.run(sensor._async_restore_state(
        SimpleNamespace(state=shown, attributes={"unit_of_measurement": unit})
    ))
    assert sensor.native_value == 221
