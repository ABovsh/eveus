"""The no-grid and grid-return blueprints, loaded from the shipped files into a real Home Assistant.

Stop Charging is what matters: turned on only once the grid has been gone for
five minutes, turned off only once it has been back for five minutes, and never
touched while One Charge is on. A real charger's Stop switch is exercised at the
end, so the blueprints are proven against the integration's own entities.
"""
from __future__ import annotations

import shutil
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
)

from .conftest import _REAL_MAIN, _entry, _mock_charger

BLUEPRINT_DIR = Path(__file__).resolve().parents[1] / "blueprints" / "automation" / "eveus"
STOP_BLUEPRINT = "stop_on_no_grid.yaml"
RESUME_BLUEPRINT = "resume_on_grid_return.yaml"
VOLTAGE, STOP, ONE_CHARGE = "sensor.grid_voltage", "switch.charger_stop", "switch.charger_one_charge"
HOLD = timedelta(minutes=5)
JUST_UNDER = HOLD - timedelta(seconds=10)
JUST_OVER = HOLD + timedelta(seconds=5)
GRID_GONE_READINGS = ["0", "6", "99.9", "unavailable", "unknown", "no reading"]


def _use(name: str, **inputs: object) -> dict:
    return {"use_blueprint": {
        "path": f"eveus/{name}",
        "input": {
            "voltage_sensor": VOLTAGE,
            "stop_switch": STOP,
            "one_charge_switch": ONE_CHARGE,
            **inputs,
        },
    }}


async def _setup(hass: HomeAssistant, *automations: dict):
    target = Path(hass.config.path("blueprints/automation/eveus"))
    target.mkdir(parents=True, exist_ok=True)
    for name in (STOP_BLUEPRINT, RESUME_BLUEPRINT):
        shutil.copy(BLUEPRINT_DIR / name, target / name)
    assert await async_setup_component(hass, "automation", {"automation": list(automations)})
    await hass.async_block_till_done()


def _services(hass: HomeAssistant) -> tuple[list, list]:
    return async_mock_service(hass, "switch", "turn_on"), async_mock_service(hass, "switch", "turn_off")


def _targets(calls) -> list[str]:
    return [
        entity
        for call in calls
        for entity in (
            [call.data["entity_id"]]
            if isinstance(call.data["entity_id"], str)
            else call.data["entity_id"]
        )
    ]


async def _set(hass: HomeAssistant, entity: str, state: object) -> None:
    hass.states.async_set(entity, str(state))
    await hass.async_block_till_done()


async def _state(hass: HomeAssistant, *, volt: object = 230, stop: str = "off", one_charge: str = "off") -> None:
    await _set(hass, VOLTAGE, volt)
    await _set(hass, STOP, stop)
    await _set(hass, ONE_CHARGE, one_charge)


class _Clock:
    """Moves the frozen clock, so a timer started after a step counts from that step."""

    def __init__(self, freezer: FrozenDateTimeFactory) -> None:
        self._freezer = freezer

    async def advance(self, hass: HomeAssistant, delta: timedelta) -> None:
        self._freezer.tick(delta)
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()


@pytest.fixture
def clock(freezer: FrozenDateTimeFactory) -> _Clock:
    return _Clock(freezer)


# --- Stop on no grid ------------------------------------------------------------


@pytest.mark.parametrize("reading", GRID_GONE_READINGS)
async def test_stop_turns_on_stop_charging_after_five_minutes_without_grid(hass, clock, reading):
    on, off = _services(hass)
    await _state(hass)
    await _setup(hass, _use(STOP_BLUEPRINT))
    await _set(hass, VOLTAGE, reading)
    await clock.advance(hass, JUST_UNDER)
    assert on == []
    await clock.advance(hass, timedelta(seconds=15))
    assert _targets(on) == [STOP]
    assert off == []


@pytest.mark.parametrize("reading", ["100", "100.1", "150", "189", "230"])
async def test_stop_ignores_a_low_grid_that_is_still_a_grid(hass, clock, reading):
    on, _ = _services(hass)
    await _state(hass)
    await _setup(hass, _use(STOP_BLUEPRINT))
    await _set(hass, VOLTAGE, reading)
    await clock.advance(hass, HOLD * 2)
    assert on == []


async def test_stop_threshold_is_configurable(hass, clock):
    on, _ = _services(hass)
    await _state(hass)
    await _setup(hass, _use(STOP_BLUEPRINT, threshold=150))
    await _set(hass, VOLTAGE, 149)
    await clock.advance(hass, JUST_OVER)
    assert _targets(on) == [STOP]


async def test_stop_a_short_outage_restarts_the_five_minutes(hass, clock):
    on, _ = _services(hass)
    await _state(hass)
    await _setup(hass, _use(STOP_BLUEPRINT))
    await _set(hass, VOLTAGE, "unavailable")
    await clock.advance(hass, JUST_UNDER)
    await _set(hass, VOLTAGE, 230)
    await clock.advance(hass, timedelta(seconds=30))
    await _set(hass, VOLTAGE, "unavailable")
    await clock.advance(hass, JUST_UNDER)
    assert on == []
    await clock.advance(hass, timedelta(seconds=15))
    assert _targets(on) == [STOP]


async def test_stop_leaves_a_deliberate_one_charge_alone(hass, clock):
    on, off = _services(hass)
    await _state(hass, one_charge="on")
    await _setup(hass, _use(STOP_BLUEPRINT))
    await _set(hass, VOLTAGE, 0)
    await clock.advance(hass, JUST_OVER)
    assert on == []
    assert off == []


@pytest.mark.parametrize(
    ("stop", "one_charge"),
    [("on", "off"), ("unavailable", "off"), ("off", "unavailable"), ("unavailable", "unavailable")],
)
async def test_stop_sends_nothing_when_stopped_already_or_the_charger_is_away(
    hass, clock, stop, one_charge,
):
    on, _ = _services(hass)
    await _state(hass, stop=stop, one_charge=one_charge)
    await _setup(hass, _use(STOP_BLUEPRINT))
    await _set(hass, VOLTAGE, 0)
    await clock.advance(hass, JUST_OVER)
    assert on == []


async def test_stop_fires_for_each_outage(hass, clock):
    on, _ = _services(hass)
    await _state(hass)
    await _setup(hass, _use(STOP_BLUEPRINT))
    for expected in (1, 2):
        await _set(hass, VOLTAGE, 0)
        await clock.advance(hass, JUST_OVER)
        assert len(on) == expected
        await _set(hass, VOLTAGE, 230)
        await clock.advance(hass, JUST_OVER)


async def test_stop_does_not_fire_for_a_sensor_already_missing_at_startup(hass, clock):
    """A restart with the sensor not loaded yet must not stop a running charge."""
    on, _ = _services(hass)
    await _state(hass, volt="unavailable")
    await _setup(hass, _use(STOP_BLUEPRINT))
    await clock.advance(hass, HOLD * 2)
    assert on == []


# --- Resume on grid return ------------------------------------------------------


@pytest.mark.parametrize("reading", ["190.1", "230", "253"])
async def test_resume_turns_off_stop_charging_after_five_minutes_of_grid(hass, clock, reading):
    on, off = _services(hass)
    await _state(hass, volt=0, stop="on")
    await _setup(hass, _use(RESUME_BLUEPRINT))
    await _set(hass, VOLTAGE, reading)
    await clock.advance(hass, JUST_UNDER)
    assert off == []
    await clock.advance(hass, timedelta(seconds=15))
    assert _targets(off) == [STOP]
    assert on == []


@pytest.mark.parametrize("reading", ["0", "150", "190", "unavailable", "unknown", "no reading"])
async def test_resume_needs_a_voltage_above_the_threshold(hass, clock, reading):
    _, off = _services(hass)
    await _state(hass, volt=0, stop="on")
    await _setup(hass, _use(RESUME_BLUEPRINT))
    await _set(hass, VOLTAGE, reading)
    await clock.advance(hass, HOLD * 2)
    assert off == []


async def test_resume_threshold_is_configurable(hass, clock):
    _, off = _services(hass)
    await _state(hass, volt=0, stop="on")
    await _setup(hass, _use(RESUME_BLUEPRINT, threshold=210))
    await _set(hass, VOLTAGE, 205)
    await clock.advance(hass, JUST_OVER)
    assert off == []
    await _set(hass, VOLTAGE, 215)
    await clock.advance(hass, JUST_OVER)
    assert _targets(off) == [STOP]


@pytest.mark.parametrize("dip", ["0", "150", "unavailable"])
async def test_resume_a_dip_restarts_the_five_minutes(hass, clock, dip):
    _, off = _services(hass)
    await _state(hass, volt=0, stop="on")
    await _setup(hass, _use(RESUME_BLUEPRINT))
    await _set(hass, VOLTAGE, 230)
    await clock.advance(hass, JUST_UNDER)
    await _set(hass, VOLTAGE, dip)
    await clock.advance(hass, timedelta(seconds=30))
    await _set(hass, VOLTAGE, 230)
    await clock.advance(hass, JUST_UNDER)
    assert off == []
    await clock.advance(hass, timedelta(seconds=15))
    assert _targets(off) == [STOP]


async def test_resume_leaves_stop_on_while_one_charge_is_on(hass, clock):
    on, off = _services(hass)
    await _state(hass, volt=0, stop="on", one_charge="on")
    await _setup(hass, _use(RESUME_BLUEPRINT))
    await _set(hass, VOLTAGE, 230)
    await clock.advance(hass, JUST_OVER)
    assert on == []
    assert off == []


@pytest.mark.parametrize(
    ("stop", "one_charge"),
    [("off", "off"), ("unavailable", "off"), ("on", "unavailable"), ("unavailable", "unavailable")],
)
async def test_resume_sends_nothing_when_not_stopped_or_the_charger_is_away(
    hass, clock, stop, one_charge,
):
    _, off = _services(hass)
    await _state(hass, volt=0, stop=stop, one_charge=one_charge)
    await _setup(hass, _use(RESUME_BLUEPRINT))
    await _set(hass, VOLTAGE, 230)
    await clock.advance(hass, JUST_OVER)
    assert off == []


async def test_resume_does_not_lift_a_stop_on_a_restart_with_the_grid_present(hass, clock):
    _, off = _services(hass)
    await _state(hass, stop="on")
    await _setup(hass, _use(RESUME_BLUEPRINT))
    await clock.advance(hass, HOLD * 2)
    assert off == []


async def test_resume_does_not_lift_a_stop_turned_on_while_the_grid_is_present(hass, clock):
    _, off = _services(hass)
    await _state(hass)
    await _setup(hass, _use(RESUME_BLUEPRINT))
    await _set(hass, STOP, "on")
    await clock.advance(hass, HOLD * 2)
    assert off == []


async def test_resume_lifts_a_stop_after_a_sensor_outage(hass, clock):
    """The sensor's own dropout looks like an outage; a real one and this one end alike."""
    _, off = _services(hass)
    await _state(hass, stop="on")
    await _setup(hass, _use(RESUME_BLUEPRINT))
    await _set(hass, VOLTAGE, "unavailable")
    await clock.advance(hass, timedelta(seconds=30))
    await _set(hass, VOLTAGE, 230)
    await clock.advance(hass, JUST_OVER)
    assert _targets(off) == [STOP]


# --- Both together --------------------------------------------------------------


async def test_a_full_outage_stops_and_resumes_exactly_once(hass, clock):
    on, off = _services(hass)
    await _state(hass)
    await _setup(hass, _use(STOP_BLUEPRINT), _use(RESUME_BLUEPRINT))
    await _set(hass, VOLTAGE, "unavailable")
    await clock.advance(hass, JUST_OVER)
    assert (_targets(on), off) == ([STOP], [])
    await _set(hass, STOP, "on")
    await _set(hass, VOLTAGE, 230)
    await clock.advance(hass, JUST_OVER)
    assert (_targets(on), _targets(off)) == ([STOP], [STOP])
    await _set(hass, STOP, "off")
    await clock.advance(hass, HOLD * 2)
    assert (len(on), len(off)) == (1, 1)


async def test_the_voltage_sag_of_a_resumed_charge_does_not_start_a_stop_cycle(hass, clock):
    """Why the two thresholds differ: the car's own load may pull the line below 190 V."""
    on, off = _services(hass)
    await _state(hass, volt=0, stop="on")
    await _setup(hass, _use(STOP_BLUEPRINT), _use(RESUME_BLUEPRINT))
    await _set(hass, VOLTAGE, 230)
    await clock.advance(hass, JUST_OVER)
    assert _targets(off) == [STOP]
    await _set(hass, STOP, "off")
    await _set(hass, VOLTAGE, 165)
    await clock.advance(hass, HOLD * 2)
    assert on == []
    assert len(off) == 1


# --- The integration's real switches --------------------------------------------


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_the_blueprints_drive_the_real_stop_charging_switch(hass, clock, aioclient_mock):
    host = "192.168.1.50"
    entry = _entry(hass, host)
    _mock_charger(aioclient_mock, host, json=dict(_REAL_MAIN, evseEnabled=0, oneCharge=0))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    stop = "switch.eveus_ev_charger_stop_charging"
    one_charge = "switch.eveus_ev_charger_one_charge"
    assert hass.states.get(stop).state == "off"
    assert hass.states.get(one_charge).state == "off"

    await _set(hass, VOLTAGE, 230)
    await _setup(
        hass,
        _use(STOP_BLUEPRINT, stop_switch=stop, one_charge_switch=one_charge),
        _use(RESUME_BLUEPRINT, stop_switch=stop, one_charge_switch=one_charge),
    )

    def commands() -> list[dict]:
        return [
            parse_qs(data)
            for method, url, data, _ in aioclient_mock.mock_calls
            if str(url).endswith("/pageEvent")
        ]

    aioclient_mock.mock_calls.clear()
    await _set(hass, VOLTAGE, "unavailable")
    await clock.advance(hass, JUST_OVER)
    assert commands() == [{"pageevent": ["evseEnabled"], "evseEnabled": ["1"]}]
    assert hass.states.get(stop).state == "on"

    # The charger now reports the stop, or the next poll would correct the switch.
    aioclient_mock.clear_requests()
    _mock_charger(aioclient_mock, host, json=dict(_REAL_MAIN, evseEnabled=1, oneCharge=0))
    await entry.runtime_data.updater.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(stop).state == "on"

    aioclient_mock.mock_calls.clear()
    await _set(hass, VOLTAGE, 230)
    await clock.advance(hass, JUST_OVER)
    assert commands() == [{"pageevent": ["evseEnabled"], "evseEnabled": ["0"]}]
