"""Golden real-Home-Assistant scenarios: the safety net for P2-P4.

Each scenario drives the mocked charger through a sequence of steps with the
``Scenario`` harness below, then pins every eveus entity's (state, attributes)
after each step with a syrupy snapshot. Two scenarios (charge_session,
outage) additionally cap the number of ``state_changed`` events a run is
allowed to publish, replacing guesswork about recorder cost with a measured
ceiling (start from the observed count + 20%).
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    STATE_UNAVAILABLE,
)
from homeassistant.core import Event, HomeAssistant, State
from homeassistant.helpers import device_registry as dr, entity_registry as er, issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_restore_state_shutdown_restart,
    mock_restore_cache,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.eveus import common_base, common_network
from custom_components.eveus.const import (
    AVAILABILITY_GRACE_PERIOD,
    CONF_BATTERY_CAPACITY,
    CONF_INITIAL_SOC,
    CONF_MODEL,
    CONF_PHASES,
    CONF_SCHEME,
    CONF_SOC_CORRECTION,
    CONF_SOC_MODE,
    CONF_TARGET_SOC,
    DOMAIN,
    EVENT_CAR_CONNECTED,
    EVENT_CAR_DISCONNECTED,
    EVENT_CHARGING_FINISHED,
    EVENT_CHARGING_STARTED,
    EVENT_ERROR,
    MODEL_16A,
    SOC_MODE_ADVANCED,
)
from custom_components.eveus.safety import safety_issue_id

from .conftest import _entry, _mock_charger, _setup

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

HOST_A = "192.168.1.60"  # NOSONAR(python:S1313) - RFC 1918 test fixture
HOST_B = "192.168.1.61"  # NOSONAR(python:S1313) - RFC 1918 test fixture

STATE_SENSOR = "sensor.eveus_ev_charger_state"
VOLTAGE = "sensor.eveus_ev_charger_voltage"
QUALITY = "sensor.eveus_ev_charger_connection_quality"
CURRENT = "number.eveus_ev_charger_charging_current"
LAST_SESSION_ENERGY = "sensor.eveus_ev_charger_last_session_energy"

_FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"
REAL_MAIN = json.loads((_FIXTURES / "real_main_response.json").read_text(encoding="utf-8"))
FW151_MAIN = json.loads(
    (_FIXTURES / "fw151_unknown_state_main.json").read_text(encoding="utf-8")
)
FW151_INIT = json.loads((_FIXTURES / "fw151_init.json").read_text(encoding="utf-8"))

_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

# Entities whose STATE (not just an attribute) is derived from the real
# wall clock rather than the mocked payload, keyed by unique_id suffix.
# Time Drift compares the fixture's fixed systemTime against real time.time().
_VOLATILE_STATE_SUFFIXES = ("time_drift",)


def _scrub(value: object) -> object:
    """Blank out wall-clock-derived values before they reach a snapshot.

    `Last Session *`'s `finished_at` attribute and `Charging Finish Time`'s
    own state are real timestamps taken at test-run time; pinning them would
    make every snapshot fail on the next run for no behavioural reason.
    """
    if isinstance(value, str) and _TIMESTAMP_RE.match(value):
        return "<TIMESTAMP>"
    return value


def _capture_states(hass: HomeAssistant) -> dict[str, tuple[object, dict]]:
    registry = er.async_get(hass)
    result: dict[str, tuple[object, dict]] = {}
    for entity in sorted(registry.entities.values(), key=lambda e: e.entity_id):
        if entity.platform != DOMAIN:
            continue
        # A just-unloaded entry's entities linger in the registry with a
        # stale cached state; how HA renders that leftover state (e.g.
        # friendly_name composition) is an HA-version implementation detail,
        # not eveus behaviour worth pinning, so skip them entirely.
        owning_entry = hass.config_entries.async_get_entry(entity.config_entry_id)
        if owning_entry is not None and owning_entry.state is not ConfigEntryState.LOADED:
            continue
        state = hass.states.get(entity.entity_id)
        if state is None:
            continue
        # HA's attribute dict keys are plain str on some versions and a
        # StrEnum (EntityStateAttribute et al) on others; normalize so the
        # snapshot text doesn't move between HA releases for the same data.
        attrs = {
            getattr(key, "value", key): _scrub(value) for key, value in state.attributes.items()
        }
        if entity.unique_id.endswith(_VOLATILE_STATE_SUFFIXES):
            value = "<DRIFT>"
        else:
            value = _scrub(state.state)
        result[entity.entity_id] = (value, attrs)
    return result


def _entity_id(hass: HomeAssistant, entry: MockConfigEntry, unique_id_suffix: str) -> str:
    registry = er.async_get(hass)
    return next(
        item.entity_id
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
        if item.unique_id.endswith(unique_id_suffix)
    )


def _calls(aioclient_mock: AiohttpClientMocker, host: str, path: str) -> list:
    return [
        call
        for call in aioclient_mock.mock_calls
        if call[1].host == host and call[1].path == path
    ]


class Scenario:
    """Drives one or more chargers through a sequence of steps.

    Captures every eveus entity's (state, attributes) after each step; the
    test asserts the full list against a snapshot. The fake monotonic clock
    mirrors the pattern already proven in
    ``test_lifecycle_smoke.py::test_failed_polls_hold_through_grace_then_expire_and_recover``:
    bumping it lets an entity's own `available` computation see elapsed time,
    while ``async_fire_time_changed`` is what actually fires a scheduled grace
    recheck (a repeated failure does not otherwise notify listeners).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        aioclient_mock: AiohttpClientMocker,
        monkeypatch: pytest.MonkeyPatch,
        host: str,
        entry: MockConfigEntry,
    ) -> None:
        self.hass = hass
        self.aioclient_mock = aioclient_mock
        self.host = host
        self.entry = entry
        self.captures: list[dict] = []
        self._elapsed = 0.0
        self._clock = {"now": time.monotonic()}
        # The outage clock is the coordinator's (common_network); entities
        # still stamp optimistic writes on common_base's.
        for module in (common_base, common_network):
            monkeypatch.setattr(
                module,
                "time",
                SimpleNamespace(monotonic=lambda: self._clock["now"], time=time.time),
            )
        self.capture()

    @property
    def updater(self):
        return self.entry.runtime_data.updater

    def capture(self) -> None:
        self.captures.append(_capture_states(self.hass))

    async def poll_for(
        self, entry: MockConfigEntry, host: str, base: dict | None = None, **overrides: object
    ) -> None:
        payload = {**(base if base is not None else REAL_MAIN), **overrides}
        self.aioclient_mock.clear_requests()
        self.aioclient_mock.post(f"http://{host}/main", json=payload)
        self.aioclient_mock.post(f"http://{host}/pageEvent", text="ok")
        await entry.runtime_data.updater.async_refresh()
        await self.hass.async_block_till_done()
        self.capture()

    async def poll(self, base: dict | None = None, **overrides: object) -> None:
        await self.poll_for(self.entry, self.host, base, **overrides)

    async def fail(self) -> None:
        self.aioclient_mock.clear_requests()
        self.aioclient_mock.post(f"http://{self.host}/main", exc=asyncio.TimeoutError())
        await self.updater.async_refresh()
        await self.hass.async_block_till_done()
        self.capture()

    async def advance(self, seconds: float) -> None:
        self._clock["now"] += seconds
        self._elapsed += seconds
        async_fire_time_changed(
            self.hass, dt_util.utcnow() + timedelta(seconds=self._elapsed)
        )
        await self.hass.async_block_till_done()
        self.capture()

    async def call(self, service: str, data: dict) -> None:
        domain, _, name = service.partition(".")
        await self.hass.services.async_call(domain, name, data, blocking=True)
        await self.hass.async_block_till_done()
        self.capture()


def _count_state_changes(hass: HomeAssistant) -> dict[str, int]:
    """Register a listener and return the running per-entity counter it fills."""
    counts: dict[str, int] = {}

    def _record(event: Event) -> None:
        entity_id = event.data["entity_id"]
        counts[entity_id] = counts.get(entity_id, 0) + 1

    hass.bus.async_listen("state_changed", _record)
    return counts


async def test_cold_boot(hass, aioclient_mock, monkeypatch, snapshot) -> None:
    """First poll from the verbatim live capture. No further steps."""
    entry = _entry(hass, HOST_A)
    _mock_charger(aioclient_mock, HOST_A)
    await _setup(hass, entry)

    scenario = Scenario(hass, aioclient_mock, monkeypatch, HOST_A, entry)

    assert scenario.captures == snapshot


async def test_turn_off_ocpp_on_classic_firmware(hass, aioclient_mock) -> None:
    entry = _entry(hass, HOST_A)
    aioclient_mock.post(f"http://{HOST_A}/main", json=dict(REAL_MAIN, ocppEnabled=1))
    aioclient_mock.post(f"http://{HOST_A}/ocppEvent", text="ok")
    aioclient_mock.post(f"http://{HOST_A}/pageEvent", status=405)
    await _setup(hass, entry)
    entity_id = "switch.eveus_ev_charger_connect_to_ocpp"
    assert hass.states.get(entity_id).state == "on"

    aioclient_mock.mock_calls.clear()
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": entity_id}, blocking=True
    )

    assert [(url.path, data) for _, url, data, _ in aioclient_mock.mock_calls] == [
        ("/ocppEvent", "ocppEnabled=0"),
        ("/pageEvent", "pageevent=ocppEnabled&ocppEnabled=0&ocppVendor=0"),
    ]
    assert hass.states.get(entity_id).state == "off"


async def test_charge_session(hass, aioclient_mock, monkeypatch, snapshot) -> None:
    """Standby -> Connected -> Charging -> Complete -> unplug.

    Pins the fired eveus_car_*/eveus_charging_* events, the Last Session
    Energy sensor and a publication-count ceiling, on top of the full
    per-step state snapshot.
    """
    entry = _entry(hass, HOST_A)
    _mock_charger(aioclient_mock, HOST_A)
    await _setup(hass, entry)
    scenario = Scenario(hass, aioclient_mock, monkeypatch, HOST_A, entry)

    fired: list[tuple[str, dict]] = []

    def _record(event: Event) -> None:
        fired.append((event.event_type, dict(event.data)))

    for event_type in (
        EVENT_CAR_CONNECTED,
        EVENT_CAR_DISCONNECTED,
        EVENT_CHARGING_STARTED,
        EVENT_CHARGING_FINISHED,
    ):
        hass.bus.async_listen(event_type, _record)
    counts = _count_state_changes(hass)

    await scenario.poll(state=3)  # Connected
    await scenario.poll(
        state=4,
        currentSet=15,
        curMeas1=15.0,
        voltMeas1=230,
        power=3500,
        sessionEnergy=3.5,
        sessionTime=1800,
        sessionMoney=17.5,
    )  # Charging
    # The charger still reports the finished session when it completes.
    await scenario.poll(
        state=5, subState=0, sessionEnergy=3.5, sessionTime=1800, sessionMoney=17.5
    )  # Car completed the charge
    await scenario.poll(state=2)  # Unplug

    assert [event_type for event_type, _ in fired] == [
        EVENT_CAR_CONNECTED,
        EVENT_CHARGING_STARTED,
        EVENT_CHARGING_FINISHED,
        EVENT_CAR_DISCONNECTED,
    ]
    finished_data = next(data for event_type, data in fired if event_type == EVENT_CHARGING_FINISHED)
    assert finished_data["reason"] == "complete"
    assert finished_data["session_energy_kwh"] == 3.5
    assert finished_data["session_duration_s"] == 1800

    assert float(hass.states.get(LAST_SESSION_ENERGY).state) == 3.5

    # Measured baseline; ceiling is +20% (see module docstring).
    assert sum(counts.values()) <= 35  # measured 29, +20%

    assert scenario.captures == snapshot


async def test_outage(hass, aioclient_mock, monkeypatch, snapshot) -> None:
    """3 failed polls: held through grace, then unavailable, then recovery.

    This is the P3 regression gate: P3 replaces the per-entity grace timer
    with one clock owned by the coordinator, and this snapshot must not move.
    """
    entry = _entry(hass, HOST_A)
    _mock_charger(aioclient_mock, HOST_A)
    await _setup(hass, entry)
    scenario = Scenario(hass, aioclient_mock, monkeypatch, HOST_A, entry)
    counts = _count_state_changes(hass)

    third = AVAILABILITY_GRACE_PERIOD // 3

    await scenario.fail()  # failure 1 (edge): held
    assert hass.states.get(VOLTAGE).state == "226.0"
    await scenario.advance(third)
    await scenario.fail()  # failure 3 (repeated, no re-notify): still held
    assert hass.states.get(VOLTAGE).state == "226.0"
    # The quick retry FIRST_FAILURE_RETRY_INTERVAL after failure 1 failed too
    # inside that advance (failure 2). HA re-notifies nothing on a repeated
    # failure, but Connection Quality describes the failing link itself:
    # 1 success of 4 polls is 25 %, not the 50 % the first failure left (live
    # 2026-09-19: froze at 95 "Good" through a five-minute outage).
    assert hass.states.get(QUALITY).state == "25"
    await scenario.advance(third)
    await scenario.fail()  # failure 3: still held (< grace so far)
    assert hass.states.get(VOLTAGE).state == "226.0"

    await scenario.advance(AVAILABILITY_GRACE_PERIOD - 2 * third + 5)  # crosses the grace window
    assert hass.states.get(VOLTAGE).state == STATE_UNAVAILABLE

    scenario.updater._next_poll_attempt = 0  # skip the offline backoff wait
    await scenario.poll()  # recovery
    assert hass.states.get(VOLTAGE).state == "226.0"

    # Measured baseline; ceiling is +20% (see module docstring).
    assert sum(counts.values()) <= 204  # measured 170, +20%

    assert scenario.captures == snapshot


async def test_reload(hass, aioclient_mock, monkeypatch, snapshot) -> None:
    """A reload mid-session keeps values and replaces the updater instance."""
    entry = _entry(hass, HOST_A)
    _mock_charger(
        aioclient_mock, HOST_A, json={**REAL_MAIN, "state": 4, "currentSet": 15, "curMeas1": 15.0}
    )
    await _setup(hass, entry)
    scenario = Scenario(hass, aioclient_mock, monkeypatch, HOST_A, entry)
    old_updater = scenario.updater

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    scenario.capture()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.updater is not old_updater
    assert old_updater._shutting_down is True
    assert hass.states.get(CURRENT).state == "15.0"

    assert scenario.captures == snapshot


async def test_restart_restore(hass, aioclient_mock, monkeypatch, snapshot) -> None:
    """RestoreEntity seeds Last Session values across a restart.

    They are event-driven (no coordinator field backs them), so a plain
    ``/main`` poll cannot reconcile them the way it does a coordinator-mirrored
    sensor; only a newly completed session does. This pins both halves: the
    restored value survives setup untouched, then a new finished session
    overwrites it.
    """
    entry = _entry(hass, HOST_A)
    mock_restore_cache(
        hass,
        [
            State(
                LAST_SESSION_ENERGY,
                "9.87",
                {"reason": "complete", "finished_at": "2026-01-01T00:00:00+00:00"},
            ),
        ],
    )
    _mock_charger(aioclient_mock, HOST_A)
    await _setup(hass, entry)
    scenario = Scenario(hass, aioclient_mock, monkeypatch, HOST_A, entry)

    assert hass.states.get(LAST_SESSION_ENERGY).state == "9.87"

    await scenario.poll(state=3)
    await scenario.poll(
        state=4,
        currentSet=15,
        curMeas1=15.0,
        voltMeas1=230,
        power=3500,
        sessionEnergy=4.2,
        sessionTime=1500,
        sessionMoney=20.0,
    )
    await scenario.poll(
        state=5, subState=0, sessionEnergy=4.2, sessionTime=1500, sessionMoney=20.0
    )

    assert float(hass.states.get(LAST_SESSION_ENERGY).state) == 4.2

    assert scenario.captures == snapshot


async def test_fw151(hass, aioclient_mock, monkeypatch, snapshot) -> None:
    """Legacy firmware: user stop 20 -> Connected, state 3 (+power) -> Charging.

    /init resolves sw_version "1.51" from ESP_SW_version once, since fw 1.x
    never sends verFWMain/firmware in /main; that 1.x version also switches
    commands to the form the 1.x web page posts.
    """
    entry = _entry(hass, HOST_A)
    aioclient_mock.post(f"http://{HOST_A}/main", json=FW151_MAIN)
    aioclient_mock.post(f"http://{HOST_A}/pageEvent", text="ok")
    aioclient_mock.post(f"http://{HOST_A}/init", json=FW151_INIT)
    await _setup(hass, entry)
    scenario = Scenario(hass, aioclient_mock, monkeypatch, HOST_A, entry)

    assert hass.states.get(STATE_SENSOR).state == "Connected"
    assert hass.states.get(_entity_id(hass, entry, "substate")).state == "Limited by User"
    device_registry = dr.async_get(hass)
    device = next(iter(dr.async_entries_for_config_entry(device_registry, entry.entry_id)))
    assert device.sw_version == "1.51"

    stop = _entity_id(hass, entry, "stop_charging")
    aioclient_mock.mock_calls.clear()
    await scenario.call("switch.turn_off", {"entity_id": stop})
    ((_, _, data, headers),) = _calls(aioclient_mock, HOST_A, "/pageEvent")
    assert data == "evseEnabled=0"
    assert headers["pageEvent"] == "evseEnabled"

    await scenario.poll(base=FW151_MAIN, state=3, curMeas1=10.0)
    assert hass.states.get(STATE_SENSOR).state == "Charging"
    assert hass.states.get(STATE_SENSOR).attributes["raw_state"] == 3

    assert scenario.captures == snapshot


async def test_fw1x_schedule_hold_without_a_car(hass, aioclient_mock, monkeypatch):
    """1.x code 18 holds the charger for schedule 1 whether or not a car is in."""
    charging = json.loads((_FIXTURES / "fw133_charging_main.json").read_text())
    held = {**charging, "state": 18, "pilot": 1, "curMeas1": 0, "powerMeas": 0}
    entry = _entry(hass, HOST_A)
    _mock_charger(aioclient_mock, HOST_A, json=held)
    aioclient_mock.post(f"http://{HOST_A}/init", json={"ESP_SW_version": 155})
    await _setup(hass, entry)
    scenario = Scenario(hass, aioclient_mock, monkeypatch, HOST_A, entry)
    await scenario.poll(base=held)

    assert hass.states.get("binary_sensor.eveus_ev_charger_car_connected").state == "off"
    assert hass.states.get(STATE_SENSOR).state == "Standby"
    assert hass.states.get(_entity_id(hass, entry, "schedule_1_enabled")).state == "on"

    await scenario.poll(base={**held, "pilot": 2})
    assert hass.states.get("binary_sensor.eveus_ev_charger_car_connected").state == "on"


async def test_fw133_stop_and_resume(hass, aioclient_mock, monkeypatch):
    charging, stopped, resumed = (
        json.loads((_FIXTURES / f"fw133_{phase}_main.json").read_text())
        for phase in ("charging", "after_stop", "after_resume")
    )
    entry = _entry(hass, HOST_A)
    _mock_charger(aioclient_mock, HOST_A, json=charging)
    aioclient_mock.post(f"http://{HOST_A}/init", json={"ESP_SW_version": 133})
    await _setup(hass, entry)
    scenario = Scenario(hass, aioclient_mock, monkeypatch, HOST_A, entry)
    stop = _entity_id(hass, entry, "stop_charging")
    counts = _count_state_changes(hass)
    car_events: list[Event] = []
    for event_type in (EVENT_CAR_CONNECTED, EVENT_CAR_DISCONNECTED):
        hass.bus.async_listen(event_type, car_events.append)

    assert scenario.updater.is_legacy_firmware
    assert hass.states.get(STATE_SENSOR).state == "Charging"
    assert hass.states.get(stop).state == "off"
    await scenario.poll(base=charging)
    assert counts.get(stop, 0) == 0

    async def set_stop(service, value, reply, expected):
        aioclient_mock.mock_calls.clear()
        await scenario.call(f"switch.{service}", {"entity_id": stop})
        ((_, _, body, headers),) = _calls(aioclient_mock, HOST_A, "/pageEvent")
        assert headers["pageEvent"] == "evseEnabled"
        assert body == f"evseEnabled={value}"

        await scenario.poll(base=reply)
        assert scenario.updater.snapshot.get_int("evseEnabled") == value
        assert hass.states.get(stop).state == expected
        writes = counts[stop]
        await scenario.poll(base=reply)
        assert counts[stop] == writes

    await set_stop("turn_on", 1, stopped, "on")
    assert hass.states.get(STATE_SENSOR).attributes["raw_state"] == 20
    # 1.33 reports pilot 4 (unknown) after Stop; the car is still plugged in.
    assert hass.states.get(STATE_SENSOR).state == "Connected"
    await set_stop("turn_off", 0, resumed, "off")
    assert hass.states.get(STATE_SENSOR).state == "Charging"
    assert hass.states.get(STATE_SENSOR).attributes["raw_state"] == 3
    assert counts[stop] == 2
    assert car_events == []


async def test_fw151_restart_with_car_plugged_in_fires_no_event(
    hass, aioclient_mock, monkeypatch
) -> None:
    """1.x code 2 reads Standby until /init proves 1.x, then Connected.

    That is the same plugged-in charger, so the poll after setup must not
    report a car being connected.
    """
    waiting = {**FW151_MAIN, "state": 2}
    entry = _entry(hass, HOST_A)
    aioclient_mock.post(f"http://{HOST_A}/main", json=waiting)
    aioclient_mock.post(f"http://{HOST_A}/init", json=FW151_INIT)
    await _setup(hass, entry)
    scenario = Scenario(hass, aioclient_mock, monkeypatch, HOST_A, entry)
    events: list[Event] = []
    for event_type in (EVENT_CAR_CONNECTED, EVENT_CAR_DISCONNECTED):
        hass.bus.async_listen(event_type, events.append)

    await scenario.poll(base=waiting)
    await scenario.poll(base=waiting)

    assert hass.states.get(STATE_SENSOR).state == "Connected"
    assert events == []


async def test_fw151_limit_stop_and_leakage_fault(hass, aioclient_mock, monkeypatch) -> None:
    """Legacy limit stops and faults reach HA sensors, events and Repairs."""
    entry = _entry(hass, HOST_A)
    aioclient_mock.post(f"http://{HOST_A}/main", json=FW151_MAIN)
    aioclient_mock.post(f"http://{HOST_A}/pageEvent", text="ok")
    aioclient_mock.post(f"http://{HOST_A}/init", json=FW151_INIT)
    await _setup(hass, entry)
    scenario = Scenario(hass, aioclient_mock, monkeypatch, HOST_A, entry)
    substate = _entity_id(hass, entry, "substate")
    reason = _entity_id(hass, entry, "not_charging_reason")
    events: list[Event] = []
    for event_type in (EVENT_CHARGING_STARTED, EVENT_CHARGING_FINISHED, EVENT_ERROR):
        hass.bus.async_listen(event_type, events.append)

    await scenario.poll(base=FW151_MAIN, state=3, curMeas1=10.0, evseEnabled=0)
    assert hass.states.get(STATE_SENSOR).state == "Charging"

    counts = _count_state_changes(hass)
    await scenario.poll(base=FW151_MAIN, state=16, evseEnabled=0)
    await scenario.poll(base=FW151_MAIN, state=16, evseEnabled=0)
    assert hass.states.get(STATE_SENSOR).state == "Charge Complete"
    assert hass.states.get(STATE_SENSOR).attributes["raw_state"] == 16
    assert hass.states.get(substate).state == "Energy Limit"
    assert hass.states.get(reason).state == "Energy Limit Reached"
    assert {entity_id: counts.get(entity_id) for entity_id in (STATE_SENSOR, substate, reason)} == {
        STATE_SENSOR: 1, substate: 1, reason: 1,
    }
    assert [event.event_type for event in events] == [
        EVENT_CHARGING_STARTED, EVENT_CHARGING_FINISHED,
    ]
    assert events[-1].data["reason"] == "limit"

    await scenario.poll(base=FW151_MAIN, state=24)
    assert hass.states.get(STATE_SENSOR).state == "Error"
    assert hass.states.get(substate).state == "Current Leak Low"
    assert hass.states.get(reason).attributes["error"] == "Current Leak Low"
    assert events[-1].event_type == EVENT_ERROR
    assert events[-1].data["error_code"] == 4
    assert (DOMAIN, safety_issue_id(entry, "leakage_detected")) in ir.async_get(hass).issues


async def test_two_chargers(hass, aioclient_mock, monkeypatch, snapshot) -> None:
    """Two devices stay isolated: commands, polls and unload don't cross over."""
    entry_a = _entry(hass, HOST_A)
    entry_b = _entry(hass, HOST_B)
    _mock_charger(aioclient_mock, HOST_A)
    _mock_charger(aioclient_mock, HOST_B)
    await _setup(hass, entry_a)  # setting up the domain loads every entry for it
    assert entry_b.state is ConfigEntryState.LOADED

    scenario = Scenario(hass, aioclient_mock, monkeypatch, HOST_A, entry_a)
    current_b = _entity_id(hass, entry_b, "charging_current")
    assert current_b != CURRENT

    await scenario.call("number.set_value", {"entity_id": current_b, "value": 9})
    assert _calls(aioclient_mock, HOST_A, "/pageEvent") == []
    assert len(_calls(aioclient_mock, HOST_B, "/pageEvent")) == 1

    await scenario.poll_for(entry_b, HOST_B, state=4, currentSet=9, curMeas1=9.0)
    assert hass.states.get(STATE_SENSOR).state == "Standby"  # charger A untouched

    assert await hass.config_entries.async_unload(entry_a.entry_id)
    await hass.async_block_till_done()
    scenario.capture()
    assert entry_b.state is ConfigEntryState.LOADED
    assert hass.states.get(current_b).state != STATE_UNAVAILABLE

    assert scenario.captures == snapshot


async def test_soc_disabled(hass, aioclient_mock, monkeypatch, snapshot) -> None:
    """The P0.4 guard, replayed through the golden-scenario harness.

    A disabled number entity never gets ``async_added_to_hass``, so SOC
    Percent must come from the calculator seeded at setup time. See also
    ``test_lifecycle_smoke.py::test_disabled_soc_correction_entity_does_not_blank_soc_percent``.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=HOST_A,
        data={
            CONF_HOST: HOST_A,
            CONF_USERNAME: "test_user",  # NOSONAR(python:S2068) - test fixture
            CONF_PASSWORD: "test_password",  # NOSONAR(python:S2068) - test fixture
            CONF_MODEL: MODEL_16A,
            CONF_SCHEME: "http",
            CONF_PHASES: 1,
            CONF_SOC_MODE: SOC_MODE_ADVANCED,
            CONF_INITIAL_SOC: 50,
            CONF_TARGET_SOC: 80,
            CONF_BATTERY_CAPACITY: 60,
            CONF_SOC_CORRECTION: 5,
        },
        options={},
    )
    entry.add_to_hass(hass)
    _mock_charger(aioclient_mock, HOST_A)
    await _setup(hass, entry)

    registry = er.async_get(hass)
    correction_entity_id = _entity_id(hass, entry, "soc_correction")
    registry.async_update_entity(correction_entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    scenario = Scenario(hass, aioclient_mock, monkeypatch, HOST_A, entry)
    soc_percent = _entity_id(hass, entry, "soc_percent")
    state = hass.states.get(soc_percent)
    assert state is not None
    assert state.state not in (None, "unknown", "unavailable")
    float(state.state)

    assert scenario.captures == snapshot


async def test_fw133_tariffs(hass, aioclient_mock, monkeypatch):
    payload = json.loads((_FIXTURES / "fw133_charging_main.json").read_text())
    entry = _entry(hass, HOST_A)
    _mock_charger(aioclient_mock, HOST_A, json=payload)
    aioclient_mock.post(f"http://{HOST_A}/init", json={"ESP_SW_version": 133})
    await _setup(hass, entry)
    scenario = Scenario(hass, aioclient_mock, monkeypatch, HOST_A, entry)
    entities = {
        key: _entity_id(hass, entry, key)
        for key in ("primary_rate_cost", "rate_2_cost", "active_rate_cost", "rate_2_status")
    }

    def state(key):
        return hass.states.get(entities[key])

    assert state("primary_rate_cost").state == "4.32"
    assert state("rate_2_cost").state == "2.16"
    assert state("rate_2_status").state == "Enabled"
    assert state("active_rate_cost").state == "2.16"
    assert state("active_rate_cost").attributes["rate_name"] == "Rate 2"
    snapshot = scenario.updater.snapshot
    clock = datetime.fromtimestamp(snapshot.charger_wall_clock_s, UTC)
    minute = clock.hour * 60 + clock.minute
    start = snapshot.get_int("tarifAStart")
    stop = snapshot.get_int("tarifAStop")
    assert (start, stop) == (23 * 60, 7 * 60)
    assert minute >= start or minute < stop

    counts = _count_state_changes(hass)
    await scenario.poll(base=payload)
    assert all(counts.get(entity_id, 0) == 0 for entity_id in entities.values())
    await scenario.poll(base=payload, systemTime=payload["systemTime"] + 8 * 3600, current_tarif=0)
    assert state("active_rate_cost").state == "4.32"
    assert counts[entities["active_rate_cost"]] == 1
    assert all(counts.get(entities[key], 0) == 0 for key in ("primary_rate_cost", "rate_2_cost"))


async def test_fw133_minimum_voltage(hass, aioclient_mock, monkeypatch, hass_storage):
    payload = json.loads((_FIXTURES / "fw133_charging_main.json").read_text())
    entry = _entry(hass, HOST_A)
    _mock_charger(aioclient_mock, HOST_A, json=payload)
    aioclient_mock.post(f"http://{HOST_A}/init", json={"ESP_SW_version": 133})
    await _setup(hass, entry)
    scenario = Scenario(hass, aioclient_mock, monkeypatch, HOST_A, entry)
    entity_id = _entity_id(hass, entry, "minimum_voltage")
    counts = _count_state_changes(hass)

    state = hass.states.get(entity_id)
    assert state.state == "unknown"
    assert state.attributes["assumed_state"] is True
    assert set(state.attributes["options"]) == {"150", "155", "160", "165", "170", "175", "180", "200"}

    await scenario.call("select.select_option", {"entity_id": entity_id, "option": "175"})
    ((_, _, body, headers),) = _calls(aioclient_mock, HOST_A, "/pageEvent")
    assert (body, headers["pageEvent"]) == ("minVoltage=175", "minVoltage")
    assert hass.states.get(entity_id).state == "175"

    await scenario.poll(base=payload)
    assert counts[entity_id] == 1

    restore = await async_mock_restore_state_shutdown_restart(hass)
    assert restore.last_states[entity_id].extra_data.as_dict() == {"last_legacy_command": 175}
    aioclient_mock.post(f"http://{HOST_A}/init", json={"ESP_SW_version": 133})
    request_count = len(_calls(aioclient_mock, HOST_A, "/pageEvent"))
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "175"
    assert hass.states.get(entity_id).attributes["assumed_state"] is True
    assert len(_calls(aioclient_mock, HOST_A, "/pageEvent")) == request_count
