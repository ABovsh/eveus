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
from datetime import timedelta
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
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.eveus import common_base
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
    MODEL_16A,
    SOC_MODE_ADVANCED,
)

from .conftest import _entry, _mock_charger, _setup

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

HOST_A = "192.168.1.60"  # NOSONAR(python:S1313) - RFC 1918 test fixture
HOST_B = "192.168.1.61"  # NOSONAR(python:S1313) - RFC 1918 test fixture

STATE_SENSOR = "sensor.eveus_ev_charger_state"
VOLTAGE = "sensor.eveus_ev_charger_voltage"
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
        state = hass.states.get(entity.entity_id)
        if state is None:
            continue
        attrs = {key: _scrub(value) for key, value in state.attributes.items()}
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
        monkeypatch.setattr(
            common_base,
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
    await scenario.poll(state=5)  # Charge Complete
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
    await scenario.fail()  # failure 2 (repeated, no re-notify): still held
    assert hass.states.get(VOLTAGE).state == "226.0"
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
    await scenario.poll(state=5)

    assert float(hass.states.get(LAST_SESSION_ENERGY).state) == 4.2

    assert scenario.captures == snapshot


async def test_fw151(hass, aioclient_mock, monkeypatch, snapshot) -> None:
    """Legacy firmware: state 20 -> Standby, state 3 (+power) -> Charging.

    /init resolves sw_version "1.51" from ESP_SW_version once, since fw 1.x
    never sends verFWMain/firmware in /main.
    """
    entry = _entry(hass, HOST_A)
    aioclient_mock.post(f"http://{HOST_A}/main", json=FW151_MAIN)
    aioclient_mock.post(f"http://{HOST_A}/pageEvent", text="ok")
    aioclient_mock.post(f"http://{HOST_A}/init", json=FW151_INIT)
    await _setup(hass, entry)
    scenario = Scenario(hass, aioclient_mock, monkeypatch, HOST_A, entry)

    assert hass.states.get(STATE_SENSOR).state == "Standby"
    device_registry = dr.async_get(hass)
    device = next(iter(dr.async_entries_for_config_entry(device_registry, entry.entry_id)))
    assert device.sw_version == "1.51"

    await scenario.poll(base=FW151_MAIN, state=3, curMeas1=10.0)
    assert hass.states.get(STATE_SENSOR).state == "Charging"
    assert hass.states.get(STATE_SENSOR).attributes["raw_state"] == 3

    assert scenario.captures == snapshot


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
