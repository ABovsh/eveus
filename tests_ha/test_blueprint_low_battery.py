"""The low-house-battery blueprint, loaded from the shipped file into a real Home Assistant.

What matters is when the Stop switch is turned on: on the battery %, on the
battery voltage, or on whichever is reached first — and never on a reading that
is missing, nor on a dip shorter than two minutes.
"""
from __future__ import annotations

import shutil
from datetime import timedelta
from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
)

BLUEPRINT = (
    Path(__file__).resolve().parents[1]
    / "blueprints" / "automation" / "eveus" / "stop_on_low_house_battery.yaml"
)
SOC, VOLT, STOP = "sensor.house_soc", "sensor.house_voltage", "switch.charger_stop"
HOLD = timedelta(minutes=2)


async def _setup(hass: HomeAssistant, **inputs: object):
    target = Path(hass.config.path("blueprints/automation/eveus"))
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy(BLUEPRINT, target / BLUEPRINT.name)
    calls = async_mock_service(hass, "switch", "turn_on")
    assert await async_setup_component(
        hass,
        "automation",
        {"automation": {"use_blueprint": {
            "path": f"eveus/{BLUEPRINT.name}",
            "input": {"stop_switch": STOP, **inputs},
        }}},
    )
    await hass.async_block_till_done()
    return calls


def _stopped(calls) -> list[str]:
    """The switches the blueprint turned on, flattened (the target arrives as a list)."""
    return [e for c in calls for e in ([c.data["entity_id"]] if isinstance(c.data["entity_id"], str) else c.data["entity_id"])]


async def _wait(hass: HomeAssistant, delta: timedelta) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + delta)
    await hass.async_block_till_done()


def _set(hass: HomeAssistant, soc: object = 80, volt: object = 52.0) -> None:
    hass.states.async_set(SOC, str(soc))
    hass.states.async_set(VOLT, str(volt))


# --- an existing automation: only the three original inputs -------------------


async def test_a_blueprint_set_up_before_the_voltage_option_still_stops_on_percent(hass):
    _set(hass)
    calls = await _setup(hass, battery_soc=SOC, threshold=40)
    _set(hass, soc=35)
    await hass.async_block_till_done()
    assert calls == []
    await _wait(hass, HOLD + timedelta(seconds=5))
    assert _stopped(calls) == [STOP]


async def test_a_dip_shorter_than_two_minutes_stops_nothing(hass):
    _set(hass)
    calls = await _setup(hass, battery_soc=SOC, threshold=40)
    _set(hass, soc=35)
    await hass.async_block_till_done()
    await _wait(hass, timedelta(seconds=90))
    _set(hass, soc=45)
    await hass.async_block_till_done()
    await _wait(hass, HOLD + timedelta(minutes=1))
    assert calls == []


@pytest.mark.parametrize("reading", ["unavailable", "unknown"])
async def test_a_missing_reading_stops_nothing(hass, reading):
    _set(hass)
    calls = await _setup(hass, battery_soc=SOC, threshold=40)
    _set(hass, soc=reading)
    await hass.async_block_till_done()
    await _wait(hass, HOLD + timedelta(minutes=1))
    assert calls == []


# --- the choice -----------------------------------------------------------------


async def test_voltage_mode_stops_on_voltage_and_ignores_the_percent(hass):
    _set(hass)
    calls = await _setup(
        hass, stop_mode="voltage", battery_voltage=VOLT, voltage_threshold=49,
    )
    _set(hass, soc=5, volt=52.0)  # % drifted to empty, voltage is fine
    await hass.async_block_till_done()
    await _wait(hass, HOLD + timedelta(seconds=5))
    assert calls == []
    _set(hass, soc=5, volt=48.5)
    await hass.async_block_till_done()
    await _wait(hass, HOLD + timedelta(minutes=1))
    assert _stopped(calls) == [STOP]


async def test_percent_mode_ignores_the_voltage(hass):
    _set(hass)
    calls = await _setup(
        hass, stop_mode="soc", battery_soc=SOC, threshold=40,
        battery_voltage=VOLT, voltage_threshold=49,
    )
    _set(hass, soc=80, volt=47.0)
    await hass.async_block_till_done()
    await _wait(hass, HOLD + timedelta(seconds=5))
    assert calls == []


@pytest.mark.parametrize(("soc", "volt"), [(35, 52.0), (80, 48.5)])
async def test_either_mode_stops_on_whichever_is_reached_first(hass, soc, volt):
    _set(hass)
    calls = await _setup(
        hass, stop_mode="either", battery_soc=SOC, threshold=40,
        battery_voltage=VOLT, voltage_threshold=49,
    )
    _set(hass, soc=soc, volt=volt)
    await hass.async_block_till_done()
    await _wait(hass, HOLD + timedelta(seconds=5))
    assert _stopped(calls) == [STOP]


async def test_either_mode_stops_once_when_both_are_low(hass):
    _set(hass)
    calls = await _setup(
        hass, stop_mode="either", battery_soc=SOC, threshold=40,
        battery_voltage=VOLT, voltage_threshold=49,
    )
    _set(hass, soc=30, volt=48.0)
    await hass.async_block_till_done()
    await _wait(hass, HOLD + timedelta(minutes=1))
    assert len(calls) == 1


async def test_either_mode_with_one_sensor_missing_still_uses_the_other(hass):
    _set(hass)
    calls = await _setup(
        hass, stop_mode="either", battery_soc=SOC, threshold=40,
        battery_voltage=VOLT, voltage_threshold=49,
    )
    _set(hass, soc="unavailable", volt=48.0)
    await hass.async_block_till_done()
    await _wait(hass, HOLD + timedelta(seconds=5))
    assert len(calls) == 1
