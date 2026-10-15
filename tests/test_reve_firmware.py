"""REVE firmware: what its ``/main`` really means, and where its settings live.

REVE keeps the ``/main`` + ``/pageEvent`` API, but a part of ``/main`` is a
projection the firmware no longer maintains (verified on a REVE Plus,
REVE_PLUS_1.0.0 / 2.4.0, 2026-10-15):

* the tariff fields return uninitialised memory, different on every poll;
* ``leakValue`` / ``leakValueH`` are raw ADC counts (the same numbers ``/debug``
  shows as ``cll`` / ``clh``), not milliamps;
* the schedule fields mirror a legacy copy: writing them changes ``/main`` but
  not the schedule the charger runs, and ``sh*Enabled`` reverts within seconds;
* ``currentSet`` written through ``/pageEvent`` reverts within a second;
* ``temperature2`` reads -60, where classic firmware reported a real plug temperature;

The real schedules and tariffs, and the charging current, live in the local
GraphQL API (``scheduleV2``, ``ratesV2``, ``stationCurrent``). These tests pin
how the integration reads and writes them, and that nothing changes for any
other firmware.
"""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from conftest import (
    EV_HELPERS,
    HelperHass,
    StreamReaderStub,
    TEST_HOST,
    TEST_PASSWORD,
    TEST_USERNAME,
    disable_state_writes,
)
import test_entity_inventory as inventory
from custom_components.eveus import common_network
from custom_components.eveus import reve
from custom_components.eveus.client import GRAPHQL_TIMEOUT_OBJ
from custom_components.eveus.const import MAX_VALID_SYSTEM_TIME
from custom_components.eveus.common_network import EveusUpdater
from custom_components.eveus.diagnostics import async_get_config_entry_diagnostics
from custom_components.eveus.reve import (
    ReveCommandUnsupported,
    apply_schedule_command,
    is_reve_payload,
    normalize_reve_payload,
    parse_extras,
)
from custom_components.eveus.safety import POLICIES, evaluate_policy_signals
from custom_components.eveus.snapshot import EveusSnapshot

_FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> Any:
    return json.loads((_FIXTURES / name).read_text())


def _reve_main() -> dict[str, Any]:
    return _load("reve_main_response.json")


def _reve_extras_body() -> dict[str, Any]:
    return _load("reve_graphql_extras.json")


def _reve_extras():
    return parse_extras(_reve_extras_body()["data"])


def _classic_main() -> dict[str, Any]:
    return _load("real_main_response.json")


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_reve_payload_is_recognised_by_its_firmware_and_maker() -> None:
    assert is_reve_payload(_reve_main()) is True
    assert is_reve_payload({"verFWWifi": "reve_plus_2.0.0"}) is True
    assert is_reve_payload({"manufacturer": " Reve "}) is True


@pytest.mark.parametrize(
    "payload",
    [
        _load("real_main_response.json"),
        _load("fw151_unknown_state_main.json"),
        {},
        {"verFWWifi": None, "manufacturer": 7},
        {"verFWWifi": "1PGRW001A-R3.05.5", "manufacturer": "EVEUS"},
        {"verFWWifi": "PREVENT_1.0", "manufacturer": "Reverb"},
    ],
)
def test_other_payloads_are_never_taken_for_reve(payload: dict[str, Any]) -> None:
    assert is_reve_payload(payload) is False


# ---------------------------------------------------------------------------
# Normalisation of /main
# ---------------------------------------------------------------------------

_SCHEDULE_KEYS = {
    f"sh{n}{field}"
    for n in (1, 2)
    for field in ("Enabled", "Start", "Stop", "CurrentEnable", "CurrentValue")
}
_SCHEDULE_ENERGY_KEYS = {f"sh{n}Energy{f}" for n in (1, 2) for f in ("Enable", "Value")}
_RATE_KEYS = {
    "tarif",
    "tarifAEnable", "tarifAValue", "tarifAStart", "tarifAStop",
    "tarifBEnable", "tarifBValue", "tarifBStart", "tarifBStop",
}


def test_unreliable_main_fields_are_dropped_without_graphql_data() -> None:
    main = _reve_main()
    payload, unsupported = normalize_reve_payload(main, None)

    for key in _RATE_KEYS | _SCHEDULE_KEYS | _SCHEDULE_ENERGY_KEYS | {
        "activeTarif", "leakValue", "leakValueH", "temperature2", "evseEnabled"
    }:
        assert key not in payload, key
        assert key in unsupported, key
    # A field REVE does not send at all is unsupported too.
    assert "ocppconnected" in unsupported
    # Everything else passes through untouched.
    for key in ("state", "subState", "currentSet", "voltMeas1", "totalEnergy",
                "oneCharge", "aiStatus", "temperature1", "verFWWifi"):
        assert payload[key] == main[key]
        assert key not in unsupported
    # The caller's payload is not mutated.
    assert main["tarif"] == 2281701420


def test_graphql_schedules_and_rates_replace_the_projection() -> None:
    payload, unsupported = normalize_reve_payload(_reve_main(), _reve_extras())

    assert {key: payload[key] for key in sorted(_SCHEDULE_KEYS)} == {
        "sh1CurrentEnable": 1, "sh1CurrentValue": 16, "sh1Enabled": 0,
        "sh1Start": 1380, "sh1Stop": 420,
        "sh2CurrentEnable": 1, "sh2CurrentValue": 16, "sh2Enabled": 0,
        "sh2Start": 540, "sh2Stop": 1080,
    }
    assert payload["tarif"] == 0
    assert payload["tarifAEnable"] == 0
    assert payload["tarifAValue"] == 0
    assert payload["tarifBEnable"] == 0
    assert not (_SCHEDULE_KEYS | _RATE_KEYS) & unsupported
    # No REVE equivalent: schedule energy caps. The active rate is worked out
    # from ratesV2: every rate is disabled here, so the base price applies.
    assert _SCHEDULE_ENERGY_KEYS <= unsupported
    assert payload["activeTarif"] == 0
    assert "activeTarif" not in unsupported


def test_rates_convert_currency_units_to_the_legacy_hundredths() -> None:
    body = _reve_extras_body()["data"]
    body = copy.deepcopy(body)
    rates = body["stationData"]["ratesV2"]
    rates["base"] = 4.32
    rates["rates"][0].update(enabled=True, value=2.16, start=1380, end=420)
    payload, _ = normalize_reve_payload(_reve_main(), parse_extras(body))

    assert payload["tarif"] == 432
    assert payload["tarifAEnable"] == 1
    assert payload["tarifAValue"] == 216
    assert (payload["tarifAStart"], payload["tarifAStop"]) == (1380, 420)


def test_a_real_plug_temperature_is_kept() -> None:
    main = _reve_main()
    main["temperature2"] = 24
    payload, unsupported = normalize_reve_payload(main, None)
    assert payload["temperature2"] == 24
    assert "temperature2" not in unsupported


def test_a_schedule_slot_reve_does_not_have_is_unsupported() -> None:
    body = copy.deepcopy(_reve_extras_body()["data"])
    del body["stationData"]["scheduleV2"][1]
    payload, unsupported = normalize_reve_payload(_reve_main(), parse_extras(body))
    assert payload["sh1Start"] == 1380
    assert {f"sh2{f}" for f in ("Enabled", "Start", "Stop", "CurrentEnable", "CurrentValue")} <= unsupported


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        {"stationData": None},
        {"stationData": {"scheduleV2": "x", "ratesV2": {"base": 0, "rates": []}}},
        {"stationData": {"scheduleV2": [{"start": 99999}], "ratesV2": {"base": 0, "rates": []}}},
        {"stationData": {"scheduleV2": [], "ratesV2": {"base": "x", "rates": []}}},
    ],
)
def test_malformed_graphql_data_is_rejected(data: Any) -> None:
    with pytest.raises(ValueError):
        parse_extras(data)


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def test_snapshot_reports_what_the_firmware_supports() -> None:
    payload, unsupported = normalize_reve_payload(_reve_main(), None)
    snapshot = EveusSnapshot.parse(payload, None, unsupported=unsupported)
    assert snapshot.supports("currentSet") is True
    assert snapshot.supports("leakValue") is False
    assert snapshot.get("leakValue") is None

    classic = EveusSnapshot.parse(_classic_main(), None)
    assert classic.unsupported == frozenset()
    assert all(classic.supports(key) for key in ("leakValue", "tarif", "sh1Enabled"))


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


async def _entities(payload: dict[str, Any], unsupported: frozenset[str]) -> dict[str, Any]:
    entry = inventory._entry()
    updater = entry.runtime_data.updater
    updater.data = payload
    updater.unsupported_fields = unsupported
    hass = HelperHass(EV_HELPERS)
    built: list[Any] = []
    for setup_fn, _expected in inventory._EXPECTED.values():
        await setup_fn(None, entry, lambda entities, **_kw: built.extend(entities))
    for entity in built:
        entity.hass = hass
        disable_state_writes(entity)
        entity._maybe_finalize_device_info = lambda: None
        entity._handle_coordinator_update()
    return {entity.unique_id: entity for entity in built}


_NO_REVE_SOURCE = {
    "eveus_leakage_current",
    "eveus_leakage_current_peak",
    "eveus_plug_temperature",
    "eveus_ocpp_connected",
    "eveus_schedule_1_energy_limit",
    "eveus_schedule_2_energy_limit",
    "eveus_schedule_1_energy_limit_enabled",
    "eveus_schedule_2_energy_limit_enabled",
}
_FROM_GRAPHQL = {
    "eveus_stop_charging",
    "eveus_schedule_1", "eveus_schedule_2",
    "eveus_schedule_1_enabled", "eveus_schedule_2_enabled",
    "eveus_schedule_1_start", "eveus_schedule_1_stop",
    "eveus_schedule_2_start", "eveus_schedule_2_stop",
    "eveus_schedule_1_current_limit", "eveus_schedule_2_current_limit",
    "eveus_schedule_1_current_limit_enabled", "eveus_schedule_2_current_limit_enabled",
    "eveus_primary_rate_cost", "eveus_rate_2_cost", "eveus_rate_3_cost",
    "eveus_rate_2_status", "eveus_rate_3_status",
    "eveus_active_rate_cost",
}


def test_entities_without_a_reve_source_are_unavailable() -> None:
    payload, unsupported = normalize_reve_payload(_reve_main(), _reve_extras())
    entities = asyncio.run(_entities(payload, unsupported))

    unavailable = {uid for uid, entity in entities.items() if not entity.available}
    # Charging Finish Time is unavailable whenever no charge runs.
    assert unavailable == _NO_REVE_SOURCE | {"eveus_charging_finish_time"}
    assert len(entities) == 85


def test_reve_entities_show_the_graphql_schedule_and_rates() -> None:
    payload, unsupported = normalize_reve_payload(_reve_main(), _reve_extras())
    entities = asyncio.run(_entities(payload, unsupported))

    assert entities["eveus_schedule_1_enabled"].is_on is False
    assert entities["eveus_schedule_1_start"].native_value.strftime("%H:%M") == "23:00"
    assert entities["eveus_schedule_2_stop"].native_value.strftime("%H:%M") == "18:00"
    assert entities["eveus_schedule_1_current_limit_enabled"].is_on is True
    assert entities["eveus_schedule_1_current_limit"].native_value == 16
    assert entities["eveus_primary_rate_cost"].native_value == 0
    assert entities["eveus_rate_2_status"].native_value == "Disabled"
    assert entities["eveus_charging_current"].native_value == 7


def test_schedule_and_rate_entities_wait_for_graphql_data() -> None:
    payload, unsupported = normalize_reve_payload(_reve_main(), None)
    entities = asyncio.run(_entities(payload, unsupported))
    for unique_id in _FROM_GRAPHQL:
        assert entities[unique_id].available is False, unique_id
    assert entities["eveus_state"].available is True


# ---------------------------------------------------------------------------
# Safety notices
# ---------------------------------------------------------------------------

_LEAKAGE = next(policy for policy in POLICIES if policy.key == "leakage_detected")
_PLUG_HEAT = next(policy for policy in POLICIES if policy.key == "plug_overheat")


def _reve_snapshot(**changes: Any) -> EveusSnapshot:
    main = _reve_main()
    main.update(changes)
    payload, unsupported = normalize_reve_payload(main, None)
    return EveusSnapshot.parse(payload, None, unsupported=unsupported)


def test_raw_adc_counts_do_not_raise_a_leakage_notice() -> None:
    # 1266 "mA" on an idle charger: an unsupported raw signal reads as absent,
    # so an already raised notice can recover instead of freezing for good.
    assert evaluate_policy_signals(_LEAKAGE, _reve_snapshot()) == (False, True)
    assert evaluate_policy_signals(_PLUG_HEAT, _reve_snapshot()) == (False, True)


def test_the_firmware_leakage_fault_still_raises_the_notice() -> None:
    trigger, recovered = evaluate_policy_signals(_LEAKAGE, _reve_snapshot(state=7, subState=2))
    assert (trigger, recovered) == (True, False)


def test_classic_missing_leak_reading_still_freezes_the_streaks() -> None:
    main = _classic_main()
    del main["leakValue"]
    assert evaluate_policy_signals(_LEAKAGE, EveusSnapshot.parse(main, None)) == (None, None)


# ---------------------------------------------------------------------------
# Schedule writes (pure)
# ---------------------------------------------------------------------------


def _items() -> list[dict[str, Any]]:
    return list(_reve_extras().schedules)


def test_a_schedule_write_changes_one_field_of_one_slot() -> None:
    items = _items()
    updated = apply_schedule_command(items, "sh2Enabled", 1)
    assert updated[0] == items[0]
    assert updated[1] == {**items[1], "enabled": True}

    assert apply_schedule_command(items, "sh1Start", 1410)[0]["start"] == 1410
    assert apply_schedule_command(items, "sh1Stop", 0)[0]["end"] == 0
    assert apply_schedule_command(items, "sh1CurrentValue", 10.0)[0]["value"] == 10
    assert apply_schedule_command(items, "sh2CurrentEnable", 0)[1]["currentLimitEnabled"] is False
    # The input is never mutated.
    assert items == _items()


@pytest.mark.parametrize(
    ("command", "value"),
    [("sh1EnergyValue", 5), ("sh2EnergyEnable", 1), ("currentSet", 7)],
)
def test_schedule_fields_reve_has_no_place_for_are_refused(command: str, value: int) -> None:
    with pytest.raises(ReveCommandUnsupported):
        apply_schedule_command(_items(), command, value)


def test_a_missing_slot_is_refused() -> None:
    with pytest.raises(ReveCommandUnsupported):
        apply_schedule_command(_items()[:1], "sh2Enabled", 1)


@pytest.mark.parametrize(("command", "value"), [("sh1Start", 1440), ("sh1Stop", -1), ("sh1CurrentValue", 0.5)])
def test_impossible_schedule_values_are_refused(command: str, value: float) -> None:
    with pytest.raises(ValueError):
        apply_schedule_command(_items(), command, value)


# ---------------------------------------------------------------------------
# Coordinator: polling and command routing over a fake charger
# ---------------------------------------------------------------------------


class _Hass:
    loop = None


class _Response:
    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self._body = body if isinstance(body, str) else json.dumps(body)

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise aiohttp.ClientResponseError(request_info=None, history=(), status=self.status)

    @property
    def content_length(self) -> int:
        return len(self._body.encode())

    @property
    def content(self) -> StreamReaderStub:
        return StreamReaderStub(self._body.encode())


class _Charger:
    """A fake charger answering /main, /pageEvent and /graphql like REVE does."""

    def __init__(self, main: dict[str, Any], *, graphql_status: int = 200) -> None:
        self.main = main
        self.graphql_status = graphql_status
        self.schedules = copy.deepcopy(_reve_extras_body()["data"]["stationData"]["scheduleV2"])
        self.rates = copy.deepcopy(_reve_extras_body()["data"]["stationData"]["ratesV2"])
        self.charging_command = 1
        self.calls: list[tuple[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> _Response:
        path = url.split(TEST_HOST, 1)[1]
        if path == "/graphql":
            body = json.loads(kwargs["data"])
            self.calls.append((path, body))
            if self.graphql_status != 200:
                return _Response(self.graphql_status, "Server has encountered an unexpected error")
            if body["query"].lstrip().startswith("mutation"):
                change = body["variables"]["input"]
                if "scheduleV2" in change:
                    self.schedules = change["scheduleV2"]
                if "stationCurrent" in change:
                    self.main["currentSet"] = change["stationCurrent"]
                if "chargingCommand" in change:
                    self.charging_command = change["chargingCommand"]
                return _Response(200, {"data": {"updateStationData": {"__typename": "UpdateStationData", "success": True}}})
            return _Response(200, {"data": {"stationData": {
                "scheduleV2": self.schedules,
                "ratesV2": self.rates,
                "chargingCommand": self.charging_command,
            }}})
        self.calls.append((path, kwargs.get("data")))
        if path == "/main":
            return _Response(200, self.main)
        return _Response(200, "OK")

    def paths(self) -> list[str]:
        return [path for path, _ in self.calls]


def _updater(monkeypatch: pytest.MonkeyPatch, charger: _Charger) -> EveusUpdater:
    monkeypatch.setattr(common_network, "async_get_clientsession", lambda hass: charger)
    monkeypatch.setattr("custom_components.eveus.common_command.asyncio.sleep", _no_sleep)
    return EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())


async def _no_sleep(*_args: object) -> None:
    return None


def test_a_reve_poll_reads_graphql_once_per_refresh_window(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)

    data = asyncio.run(updater._async_update_data())
    asyncio.run(updater._async_update_data())

    assert charger.paths() == ["/main", "/graphql", "/main"]
    assert data["sh1Start"] == 1380
    assert "leakValue" not in data
    assert updater.snapshot.supports("sh1Start") is True
    assert updater.snapshot.supports("leakValue") is False
    assert updater.firmware_family == "reve"


def test_a_failing_graphql_never_fails_the_poll_and_backs_off(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main(), graphql_status=500)
    updater = _updater(monkeypatch, charger)

    data = asyncio.run(updater._async_update_data())
    asyncio.run(updater._async_update_data())

    assert charger.paths() == ["/main", "/graphql", "/main"]
    assert data["state"] == 2
    assert updater.snapshot.supports("sh1Enabled") is False
    assert updater.last_update_success is True


def test_classic_firmware_never_touches_graphql(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_classic_main())
    updater = _updater(monkeypatch, charger)

    data = asyncio.run(updater._async_update_data())
    ok = asyncio.run(updater._command_manager.send_command("currentSet", 10))
    ok_schedule = asyncio.run(updater._command_manager.send_command("sh1Enabled", 1))

    assert data == _classic_main()
    assert updater.snapshot.unsupported == frozenset()
    assert updater.firmware_family == "classic"
    assert ok
    assert ok_schedule
    assert charger.paths() == ["/main", "/pageEvent", "/pageEvent"]


def test_reve_charging_current_is_written_through_graphql(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    charger.calls.clear()

    ok = asyncio.run(updater._command_manager.send_command("currentSet", 10.0))

    assert ok is True
    assert charger.paths() == ["/graphql"]
    body = charger.calls[0][1]
    assert body["operationName"] == "UpdateStationData"
    assert body["variables"] == {"input": {"stationCurrent": 10}}
    assert charger.main["currentSet"] == 10


def test_reve_schedule_write_rereads_then_replaces_the_whole_list(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    charger.calls.clear()
    before = copy.deepcopy(charger.schedules)

    ok = asyncio.run(updater._command_manager.send_command("sh2Enabled", 1))

    assert ok is True
    assert charger.paths() == ["/graphql", "/graphql"]
    read, write = (body for _, body in charger.calls)
    assert read["query"].lstrip().startswith("query")
    assert write["variables"] == {"input": {"scheduleV2": [before[0], {**before[1], "enabled": True}]}}
    # The written list is what the next poll shows, without another read —
    # and Stop, read alongside the schedules, is kept.
    charger.calls.clear()
    data = asyncio.run(updater._async_update_data())
    assert data["sh2Enabled"] == 1
    assert data["evseEnabled"] == 0
    assert updater.snapshot.supports("evseEnabled") is True
    assert charger.paths() == ["/main"]


def test_reve_schedule_energy_writes_are_refused_without_a_request(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    charger.calls.clear()

    ok = asyncio.run(updater._command_manager.send_command("sh1EnergyValue", 5))

    assert ok is False
    assert charger.calls == []


def test_reve_settings_with_a_working_page_event_still_use_it(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    charger.calls.clear()

    for command, value in (("aiMode", 0), ("oneCharge", 0), ("minVoltage", 180)):
        assert asyncio.run(updater._command_manager.send_command(command, value)) is True

    assert charger.paths() == ["/pageEvent"] * 3


def test_a_rejected_graphql_write_fails_the_command(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    real_post = charger.post

    def _reject(url: str, **kwargs: Any) -> _Response:
        if url.endswith("/graphql") and json.loads(kwargs["data"])["query"].lstrip().startswith("mutation"):
            return _Response(200, {"data": {"updateStationData": {"success": False}}})
        return real_post(url, **kwargs)

    charger.post = _reject
    assert asyncio.run(updater._command_manager.send_command("currentSet", 10)) is False


def test_diagnostics_name_the_firmware_family_and_its_unsupported_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    entry = SimpleNamespace(
        title="Eveus Charger",
        data={"host": TEST_HOST, "username": TEST_USERNAME, "password": TEST_PASSWORD},
        runtime_data=SimpleNamespace(updater=updater, device_number=1),
    )

    diagnostics = asyncio.run(async_get_config_entry_diagnostics(None, entry))

    assert diagnostics["device"]["firmware_family"] == "reve"
    assert "leakValue" in diagnostics["reve"]["unsupported_fields"]
    assert diagnostics["reve"]["schedules"] == 2
    assert diagnostics["reve"]["graphql_ok"] is True
    assert "ocppAuthKey" not in json.dumps(diagnostics)


# ---------------------------------------------------------------------------
# Edges of the GraphQL data and transport
# ---------------------------------------------------------------------------


def _extras_with(schedule_changes: dict[str, Any] | None = None, **station: Any) -> dict[str, Any]:
    body = copy.deepcopy(_reve_extras_body()["data"])
    if schedule_changes:
        body["stationData"]["scheduleV2"][0].update(schedule_changes)
    body["stationData"].update(station)
    return body


def test_weekdays_are_read_as_a_list_or_a_bitmask() -> None:
    assert parse_extras(_extras_with({"weekdays": 0b1000001})).schedules[0]["weekdays"] == [0, 6]
    assert parse_extras(_extras_with({"weekdays": [3, 1, 3]})).schedules[0]["weekdays"] == [1, 3]
    assert parse_extras(_extras_with({"enabled": 1})).schedules[0]["enabled"] is True
    assert parse_extras(_extras_with({"name": None})).schedules[0]["name"] == ""


@pytest.mark.parametrize(
    "changes",
    [
        {"weekdays": [7]},
        {"weekdays": 128},
        {"weekdays": "mon"},
        {"name": 5},
        {"enabled": "yes"},
        {"enabled": 1.0},
        {"value": -1},
        {"value": float("nan")},
        {"value": True},
        {"start": 1.5},
    ],
)
def test_impossible_schedule_items_are_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        parse_extras(_extras_with(changes))


@pytest.mark.parametrize(
    "station",
    [
        {"scheduleV2": ["x"]},
        {"ratesV2": {"base": 0, "rates": ["x"]}},
        {"ratesV2": {"base": 0, "rates": None}},
    ],
)
def test_impossible_lists_are_rejected(station: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        parse_extras(_extras_with(**station))


def _clock(monkeypatch: pytest.MonkeyPatch, start: float = 1000.0) -> list[float]:
    now = [start]
    monkeypatch.setattr("custom_components.eveus.reve.time.monotonic", lambda: now[0])
    return now


def test_a_stale_read_stops_standing_for_the_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    assert updater.snapshot.supports("sh1Enabled") is True

    # GraphQL stops answering; the last read holds for an hour, then the
    # schedule entities go unavailable instead of showing an old value.
    charger.graphql_status = 500
    now[0] += reve.EXTRAS_REFRESH_SECONDS + 1
    asyncio.run(updater._async_update_data())
    assert updater.snapshot.supports("sh1Enabled") is True
    now[0] += reve.EXTRAS_MAX_AGE_SECONDS
    asyncio.run(updater._async_update_data())
    assert updater.snapshot.supports("sh1Enabled") is False
    assert updater.reve_diagnostics()["graphql_ok"] is False


def test_a_failed_read_is_retried_after_the_back_off(monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main(), graphql_status=500)
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    charger.graphql_status = 200
    now[0] += reve.EXTRAS_RETRY_SECONDS - 1
    asyncio.run(updater._async_update_data())
    assert updater.snapshot.supports("sh1Enabled") is False
    now[0] += 2
    asyncio.run(updater._async_update_data())
    assert updater.snapshot.supports("sh1Enabled") is True
    assert charger.paths().count("/graphql") == 2


@pytest.mark.parametrize(
    "body",
    ["OK", "[]", '{"errors": [{"message": "boom"}]}', '{"data": null}'],
)
def test_unusable_graphql_replies_are_errors(monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    real_post = charger.post

    def _reply(url: str, **kwargs: Any) -> _Response:
        if url.endswith("/graphql"):
            charger.calls.append(("/graphql", kwargs.get("data")))
            return _Response(200, body)
        return real_post(url, **kwargs)

    charger.post = _reply
    asyncio.run(updater._async_update_data())
    assert updater.snapshot.supports("sh1Enabled") is False
    assert updater.reve_diagnostics()["last_error"] in {"JSONDecodeError", "PayloadError", "GraphQLError"}


def test_a_plain_true_mutation_result_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    real_post = charger.post

    def _true(url: str, **kwargs: Any) -> _Response:
        if url.endswith("/graphql") and json.loads(kwargs["data"])["query"].lstrip().startswith("mutation"):
            return _Response(200, {"data": {"updateStationData": True}})
        return real_post(url, **kwargs)

    charger.post = _true
    assert asyncio.run(updater._command_manager.send_command("currentSet", 9)) is True


def test_a_cancelled_read_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    real_post = charger.post

    def _cancel(url: str, **kwargs: Any) -> _Response:
        if url.endswith("/graphql"):
            raise asyncio.CancelledError
        return real_post(url, **kwargs)

    charger.post = _cancel
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(updater._async_update_data())


# ---------------------------------------------------------------------------
# Exact boundaries and request details
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "value"),
    [("sh1Enabled", 2), ("sh2CurrentEnable", 3), ("sh1Stop", 1440), ("sh2Start", 2000)],
)
def test_each_schedule_field_is_checked_by_its_own_rule(command: str, value: int) -> None:
    # A flag takes only 0/1 and a time only a minute of the day; a rule
    # applied to the wrong field would let these through.
    with pytest.raises(ValueError):
        apply_schedule_command(_items(), command, value)


def test_only_two_schedules_are_projected() -> None:
    body = copy.deepcopy(_reve_extras_body()["data"])
    third = {**body["stationData"]["scheduleV2"][0], "name": "third"}
    body["stationData"]["scheduleV2"].append(third)
    payload, _ = normalize_reve_payload(_reve_main(), parse_extras(body))
    assert not any(key.startswith("sh3") for key in payload)


def test_a_full_week_bitmask_is_every_day() -> None:
    assert parse_extras(_extras_with({"weekdays": 127})).schedules[0]["weekdays"] == list(range(7))


def test_rates_that_are_not_an_object_are_rejected() -> None:
    with pytest.raises(ValueError):
        parse_extras(_extras_with(ratesV2="x"))


def test_energy_routes_refuse_with_their_own_error() -> None:
    api = reve.ReveApi(object())
    handler = api.route("sh2EnergyValue")
    with pytest.raises(ReveCommandUnsupported):
        asyncio.run(handler(5))
    assert api.route("aiMode") is None


def test_graphql_requests_carry_the_session_auth_timeout_and_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    charger = _Charger(_reve_main())
    seen: list[dict[str, Any]] = []
    real_post = charger.post

    def _record(url: str, **kwargs: Any) -> _Response:
        if url.endswith("/graphql"):
            seen.append(kwargs)
        return real_post(url, **kwargs)

    charger.post = _record
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    asyncio.run(updater._command_manager.send_command("currentSet", 8))

    read, write = seen
    assert read["auth"] is updater.basic_auth
    assert read["timeout"] is GRAPHQL_TIMEOUT_OBJ
    assert read["allow_redirects"] is False
    assert json.loads(read["data"])["operationName"] == "StationData"
    assert json.loads(write["data"])["operationName"] == "UpdateStationData"
    assert read["headers"]["Content-Type"] == "application/json"


def test_reads_refresh_exactly_at_the_window_and_retry_exactly_at_the_back_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    now[0] += reve.EXTRAS_REFRESH_SECONDS
    asyncio.run(updater._async_update_data())
    assert charger.paths().count("/graphql") == 2

    charger.graphql_status = 500
    now[0] += reve.EXTRAS_REFRESH_SECONDS
    asyncio.run(updater._async_update_data())
    now[0] += reve.EXTRAS_RETRY_SECONDS
    asyncio.run(updater._async_update_data())
    assert charger.paths().count("/graphql") == 4
    assert updater.reve_diagnostics()["read_failures"] == 2


def test_reve_diagnostics_report_the_read_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    now[0] += 7

    diagnostics = updater.reve_diagnostics()
    unsupported = diagnostics.pop("unsupported_fields")
    assert diagnostics == {
        "graphql_ok": True,
        "schedules": 2,
        "rates": 4,
        "read_age_seconds": 7,
        "read_failures": 0,
        "last_error": None,
    }
    assert "leakValue" in unsupported
    assert unsupported == sorted(unsupported)

    charger.graphql_status = 500
    now[0] += reve.EXTRAS_REFRESH_SECONDS
    asyncio.run(updater._async_update_data())
    after = updater.reve_diagnostics()
    assert (after["graphql_ok"], after["read_failures"], after["last_error"]) == (
        False, 1, "ClientResponseError",
    )


def test_classic_coordinator_has_no_reve_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_classic_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    assert updater.reve_diagnostics() is None
    assert updater.reve_command_route("currentSet") is None


def test_a_graphql_write_clears_the_command_failure_streak(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    manager = updater._command_manager
    assert asyncio.run(manager.send_command("sh1EnergyValue", 5)) is False
    assert manager.consecutive_failures == 1

    assert asyncio.run(manager.send_command("currentSet", 9)) is True
    assert manager.consecutive_failures == 0


def test_a_partial_graphql_result_with_errors_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    # GraphQL can return data AND errors (a partial result); trusting the data
    # would take a half-read schedule list for the whole one.
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    real_post = charger.post

    def _partial(url: str, **kwargs: Any) -> _Response:
        if url.endswith("/graphql"):
            body = {**_reve_extras_body(), "errors": [{"message": "ratesV2 failed"}]}
            return _Response(200, body)
        return real_post(url, **kwargs)

    charger.post = _partial
    asyncio.run(updater._async_update_data())
    assert updater.snapshot.supports("sh1Enabled") is False
    assert updater.reve_diagnostics()["last_error"] == "GraphQLError"


# ---------------------------------------------------------------------------
# Stop Charging on REVE: the vendor's own start/stop command
# ---------------------------------------------------------------------------
# Verified idle on REVE_PLUS_1.0.0 (2026-10-15): chargingCommand=2 puts the
# charger in subState 9 (waiting for activation) until a start command, while
# evseEnabled=1 changes nothing visible (on classic firmware Stop showed
# subState 1). The vendor app starts and stops only through chargingCommand.


@pytest.mark.parametrize(("command", "stopped"), [(2, 1), (1, 0), (0, 0)])
def test_stop_reads_the_charging_command(command: int, stopped: int) -> None:
    body = copy.deepcopy(_reve_extras_body()["data"])
    body["stationData"]["chargingCommand"] = command
    payload, unsupported = normalize_reve_payload(_reve_main(), parse_extras(body))
    assert payload["evseEnabled"] == stopped
    assert "evseEnabled" not in unsupported


@pytest.mark.parametrize("command", [None, 3, -1, "2", True])
def test_an_unusable_charging_command_leaves_stop_unsupported(command: Any) -> None:
    body = copy.deepcopy(_reve_extras_body()["data"])
    if command is None:
        del body["stationData"]["chargingCommand"]
    else:
        body["stationData"]["chargingCommand"] = command
    extras = parse_extras(body)
    payload, unsupported = normalize_reve_payload(_reve_main(), extras)
    assert "evseEnabled" not in payload
    assert "evseEnabled" in unsupported
    # The schedules and rates still come through.
    assert payload["sh1Start"] == 1380


def test_reve_stop_and_resume_go_through_the_charging_command(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    charger.calls.clear()

    assert asyncio.run(updater._command_manager.send_command("evseEnabled", 1)) is True
    assert asyncio.run(updater._command_manager.send_command("evseEnabled", 0)) is True

    assert charger.paths() == ["/graphql", "/graphql"]
    stop, resume = (body["variables"] for _, body in charger.calls)
    assert stop == {"input": {"chargingCommand": 2}}
    assert resume == {"input": {"chargingCommand": 1}}


def test_the_stop_written_is_what_the_next_poll_shows(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    asyncio.run(updater._command_manager.send_command("evseEnabled", 1))
    charger.calls.clear()

    data = asyncio.run(updater._async_update_data())

    assert data["evseEnabled"] == 1
    assert charger.paths() == ["/main"]


def test_a_long_graphql_outage_does_not_blank_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # Measured on REVE_PLUS_1.0.0 (2026-10-15, only Home Assistant polling):
    # GraphQL stopped answering for ~20, ~38 and ~5 minutes within two hours.
    # Schedules, rates and Stop are settings, so the last good read stands
    # through such a window instead of flapping ~30 entities to unavailable.
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    charger.graphql_status = 500
    for _ in range(8):  # 40 minutes of polls
        now[0] += 300
        asyncio.run(updater._async_update_data())
    assert updater.snapshot.supports("sh1Enabled") is True
    assert updater.snapshot.supports("evseEnabled") is True


def test_graphql_is_read_every_five_minutes_while_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    for _ in range(10):  # ten polls a minute apart
        asyncio.run(updater._async_update_data())
        now[0] += 60
    assert charger.paths().count("/graphql") == 2


# ---------------------------------------------------------------------------
# REVE API diagnostic sensor
# ---------------------------------------------------------------------------


def _binary_setup(updater: Any) -> tuple[list[Any], list[Any]]:
    from types import SimpleNamespace

    # The real coordinator schedules its next poll when a listener is added;
    # these tests drive the polls themselves.
    updater._schedule_refresh = lambda: None

    from custom_components.eveus import EveusRuntimeData
    from custom_components.eveus.binary_sensor import async_setup_entry

    added: list[Any] = []
    unloads: list[Any] = []
    entry = SimpleNamespace(
        runtime_data=EveusRuntimeData(
            updater=updater, device_number=1, title="Eveus Charger",
            soc_calculator=None, soc_limit=None, phases=1,
        ),
        async_on_unload=unloads.append,
    )
    asyncio.run(async_setup_entry(None, entry, lambda entities, **_kw: added.extend(entities)))
    return added, unloads


def test_reve_api_sensor_reports_whether_graphql_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    added, _ = _binary_setup(updater)
    api = next(e for e in added if e.unique_id == "eveus_reve_api")
    disable_state_writes(api)
    api._maybe_finalize_device_info = lambda: None

    assert api.available is True
    assert api.is_on is True
    assert api.entity_category == "diagnostic"
    assert api.device_class == "connectivity"

    charger.graphql_status = 500
    now[0] += reve.EXTRAS_REFRESH_SECONDS
    asyncio.run(updater._async_update_data())
    assert api.is_on is False
    # The settings it feeds still stand on the last read meanwhile.
    assert updater.snapshot.supports("sh1Enabled") is True


def test_classic_chargers_get_no_reve_api_sensor(monkeypatch: pytest.MonkeyPatch) -> None:
    charger = _Charger(_classic_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    added, unloads = _binary_setup(updater)
    assert "eveus_reve_api" not in {e.unique_id for e in added}
    assert len(added) == 3
    # Waiting for a REVE reply that never comes costs one listener, removed on unload.
    assert len(unloads) == 1


def test_reve_api_sensor_appears_when_the_charger_first_answers_as_reve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Set up while the charger was switched off: the family is unknown yet.
    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    added, _ = _binary_setup(updater)
    assert "eveus_reve_api" not in {e.unique_id for e in added}

    asyncio.run(updater._async_update_data())
    updater.async_update_listeners()
    updater.async_update_listeners()

    assert [e.unique_id for e in added].count("eveus_reve_api") == 1


def test_reve_api_sensor_belongs_to_its_own_charger(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from custom_components.eveus.binary_sensor import async_setup_entry

    charger = _Charger(_reve_main())
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())
    updater._schedule_refresh = lambda: None
    added: list[Any] = []
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(updater=updater, device_number=2),
        async_on_unload=lambda _unsub: None,
    )
    asyncio.run(async_setup_entry(None, entry, lambda entities, **_kw: added.extend(entities)))
    assert "eveus2_reve_api" in {e.unique_id for e in added}


def test_main_latency_leaves_out_the_graphql_read(monkeypatch: pytest.MonkeyPatch) -> None:
    # Connection Quality's latency describes /main. The GraphQL read that a
    # REVE poll makes after /main (up to GRAPHQL_TIMEOUT when it hangs) was
    # counted into it, so a hung GraphQL inflated the /main latency.
    now = _clock(monkeypatch)
    charger = _Charger(_reve_main())
    real_post = charger.post

    def slow_graphql(url: str, **kwargs: Any) -> _Response:
        if url.endswith("/graphql"):
            now[0] += 5.0
        return real_post(url, **kwargs)

    charger.post = slow_graphql
    updater = _updater(monkeypatch, charger)
    asyncio.run(updater._async_update_data())

    assert charger.paths() == ["/main", "/graphql"]
    assert updater.connection_quality["latency_avg"] < 1.0


# ---------------------------------------------------------------------------
# Active rate: worked out from ratesV2, the way the vendor app does
# ---------------------------------------------------------------------------
# REVE keeps no active-rate field (vendor, 2026-10-15: the app overlays the
# rates itself). The charger's wall clock (`systemTime`, already shifted by
# `timeZone`) is the clock its windows run on.


def _wall(day: int, hour: int, minute: int = 0) -> int:
    """Charger wall clock on 2026-10-<day>; the 15th is a Thursday (REVE weekday 4)."""
    from datetime import datetime, timezone

    return int(datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc).timestamp())


def _rate(start: int, end: int, value: float, *, enabled: bool = True,
          weekdays: Any = (0, 1, 2, 3, 4, 5, 6)) -> dict[str, Any]:
    return {"enabled": enabled, "currentLimitEnabled": False, "start": start, "end": end,
            "value": value, "name": "Rate", "weekdays": list(weekdays)
            if isinstance(weekdays, tuple) else weekdays}


def _active(rates: list[dict[str, Any]], clock: int | None, base: float = 4.32) -> dict[str, Any]:
    extras = parse_extras(_extras_with(ratesV2={"base": base, "rates": rates}))
    main = _reve_main()
    if clock is None:
        del main["systemTime"]
    else:
        main["systemTime"] = clock
    payload, unsupported = normalize_reve_payload(main, extras)
    return {"activeTarif": payload.get("activeTarif"), "unsupported": "activeTarif" in unsupported}


_NIGHT = _rate(23 * 60, 7 * 60, 2.16)  # Anton's night rate, every day


def test_outside_every_window_the_base_price_is_active() -> None:
    assert _active([_NIGHT], _wall(15, 14, 24)) == {"activeTarif": 0, "unsupported": False}


def test_inside_a_window_that_rate_is_active_including_past_midnight() -> None:
    assert _active([_NIGHT], _wall(15, 23, 0))["activeTarif"] == 1
    assert _active([_NIGHT], _wall(16, 2, 0))["activeTarif"] == 1
    assert _active([_NIGHT], _wall(16, 6, 59))["activeTarif"] == 1
    # The end minute belongs to the next rate.
    assert _active([_NIGHT], _wall(16, 7, 0))["activeTarif"] == 0
    assert _active([_NIGHT], _wall(15, 22, 59))["activeTarif"] == 0


def test_the_second_rate_maps_to_rate_3() -> None:
    day = _rate(9 * 60, 18 * 60, 5.0)
    assert _active([_NIGHT, day], _wall(15, 12, 0))["activeTarif"] == 2


def test_a_disabled_rate_never_applies() -> None:
    assert _active([_rate(0, 1439, 1.0, enabled=False)], _wall(15, 12, 0))["activeTarif"] == 0


def test_a_rate_applies_only_on_its_weekdays() -> None:
    weekdays_only = _rate(9 * 60, 18 * 60, 5.0, weekdays=(1, 2, 3, 4, 5))
    assert _active([weekdays_only], _wall(16, 12, 0))["activeTarif"] == 1  # Friday
    assert _active([weekdays_only], _wall(17, 12, 0))["activeTarif"] == 0  # Saturday


def test_past_midnight_a_window_belongs_to_the_day_it_started() -> None:
    friday_night = _rate(23 * 60, 7 * 60, 2.0, weekdays=(5,))
    assert _active([friday_night], _wall(16, 23, 30))["activeTarif"] == 1  # Friday 23:30
    assert _active([friday_night], _wall(17, 2, 0))["activeTarif"] == 1  # Saturday 02:00, Friday's night
    assert _active([friday_night], _wall(15, 2, 0))["activeTarif"] == 0  # Thursday 02:00, Wednesday's night


def test_weekdays_given_as_a_bitmask_count_the_same() -> None:
    weekdays_only = _rate(9 * 60, 18 * 60, 5.0, weekdays=0b0111110)  # Monday-Friday
    assert _active([weekdays_only], _wall(16, 12, 0))["activeTarif"] == 1
    assert _active([weekdays_only], _wall(17, 12, 0))["activeTarif"] == 0


def test_overlapping_windows_resolve_to_the_first_rate() -> None:
    # The vendor has not said how the app resolves an overlap; the first
    # enabled rate in the list wins, as the classic windows model does.
    wide = _rate(8 * 60, 20 * 60, 5.0)
    narrow = _rate(10 * 60, 12 * 60, 1.0)
    assert _active([wide, narrow], _wall(15, 11, 0))["activeTarif"] == 1
    assert _active([narrow, wide], _wall(15, 11, 0))["activeTarif"] == 1


def test_a_third_or_fourth_rate_has_no_entity_so_the_active_rate_is_unknown() -> None:
    off = _rate(0, 0, 0, enabled=False)
    third = _rate(9 * 60, 18 * 60, 5.0)
    assert _active([off, off, third], _wall(15, 12, 0)) == {"activeTarif": None, "unsupported": True}
    assert _active([off, off, third], _wall(15, 20, 0))["activeTarif"] == 0


def test_a_zero_length_window_makes_the_active_rate_unknown() -> None:
    # Whole day or never: the app's meaning is unknown, so no guess.
    assert _active([_rate(600, 600, 5.0)], _wall(15, 12, 0))["unsupported"] is True


def test_without_the_charger_clock_the_active_rate_is_unknown() -> None:
    assert _active([_NIGHT], None)["unsupported"] is True


def test_unreadable_weekdays_leave_the_tariffs_but_not_the_active_rate() -> None:
    extras = parse_extras(_extras_with(ratesV2={"base": 4.32, "rates": [
        {**_NIGHT, "weekdays": "every day"},
    ]}))
    main = _reve_main()
    main["systemTime"] = _wall(15, 14, 0)
    payload, unsupported = normalize_reve_payload(main, extras)
    assert payload["tarif"] == 432
    assert payload["tarifAValue"] == 216
    assert "activeTarif" in unsupported


def test_active_rate_cost_and_cost_to_target_follow_the_reve_rates() -> None:
    extras = parse_extras(_extras_with(ratesV2={"base": 4.32, "rates": [_NIGHT]}))
    main = _reve_main()
    main["systemTime"] = _wall(16, 2, 0)
    payload, unsupported = normalize_reve_payload(main, extras)
    entities = asyncio.run(_entities(payload, unsupported))

    assert entities["eveus_active_rate_cost"].available is True
    assert entities["eveus_active_rate_cost"].native_value == 2.16
    assert entities["eveus_active_rate_cost"].extra_state_attributes == {"rate_name": "Rate 2"}
    assert entities["eveus_cost_to_target_soc"].available is True


def test_a_daytime_window_starts_on_its_start_minute_and_ends_before_its_end() -> None:
    day = _rate(9 * 60, 18 * 60, 5.0)
    assert _active([day], _wall(15, 9, 0))["activeTarif"] == 1
    assert _active([day], _wall(15, 8, 59))["activeTarif"] == 0
    assert _active([day], _wall(15, 18, 0))["activeTarif"] == 0


def test_saturday_night_runs_into_sunday_morning() -> None:
    saturday_night = _rate(23 * 60, 7 * 60, 2.0, weekdays=(6,))
    assert _active([saturday_night], _wall(18, 2, 0))["activeTarif"] == 1  # Sunday 02:00


@pytest.mark.parametrize(
    ("changes", "known"),
    [
        ({"timeZone": None}, False),
        ({"systemTime": True}, False),
        ({"systemTime": 0}, False),
        ({"systemTime": 1}, True),
        ({"systemTime": MAX_VALID_SYSTEM_TIME - 1}, True),
        ({"systemTime": MAX_VALID_SYSTEM_TIME}, False),
    ],
)
def test_the_active_rate_needs_a_sane_charger_clock(changes: dict[str, Any], known: bool) -> None:
    from custom_components.eveus.reve import _wall_clock

    main = {"systemTime": _wall(15, 12, 0), "timeZone": 3, **changes}
    if main["timeZone"] is None:
        del main["timeZone"]
    assert (_wall_clock(main) is not None) is known
