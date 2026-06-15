"""EveusSetpointNumber scaling, clamping, and command wiring."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.number import NumberMode

from custom_components.eveus import number as number_mod
from custom_components.eveus.number import (
    EveusSetpointNumber,
    EveusSetpointNumberDescription,
)

ENERGY = EveusSetpointNumberDescription(
    key="limit_energy",
    name="Limit Energy",
    command="energyLimit",
    state_key="energyLimit",
    device_to_ha=1.0,        # charger reports kWh already
    ha_to_device=1000.0,     # but writing wants Wh-thousandths
    native_min_value=0.0,
    native_max_value=100.0,
    native_step=1.0,
    native_unit_of_measurement="kWh",
)

TIME = EveusSetpointNumberDescription(
    key="limit_time",
    name="Limit Time",
    command="timeLimit",
    state_key="timeLimit",
    device_to_ha=1 / 60,     # seconds -> minutes
    ha_to_device=60.0,       # minutes -> seconds
    native_min_value=0.0,
    native_max_value=1440.0,
    native_step=5.0,
    native_unit_of_measurement="min",
)


def _make(description):
    updater = MagicMock()
    updater.available = True
    updater.data = {description.state_key: 0}
    updater.send_command = AsyncMock(return_value=True)
    updater.config_entry = MagicMock()
    ent = EveusSetpointNumber(updater, description, device_number=1)
    ent.hass = MagicMock()
    ent.async_write_ha_state = MagicMock()
    return ent, updater


def test_energy_reads_one_to_one_writes_times_1000():
    ent, updater = _make(ENERGY)
    updater.data = {"energyLimit": 57}            # charger reports 57 kWh
    assert ent._read_device_value() == 57.0       # HA shows 57 kWh (device_to_ha=1)
    asyncio.run(ent.async_set_native_value(40))
    # The WRITE must be the ×1000 form, not 40.
    updater.send_command.assert_awaited_once_with("energyLimit", 40000)


def test_time_reads_seconds_as_minutes_writes_minutes_as_seconds():
    ent, updater = _make(TIME)
    updater.data = {"timeLimit": 3600}
    assert ent._read_device_value() == 60.0       # 3600 s -> 60 min
    asyncio.run(ent.async_set_native_value(30))
    updater.send_command.assert_awaited_once_with("timeLimit", 1800)


def test_value_is_clamped_to_range_before_write():
    ent, updater = _make(ENERGY)
    asyncio.run(ent.async_set_native_value(99999))
    updater.send_command.assert_awaited_once_with("energyLimit", 100000)  # 100 kWh max ×1000


def test_unique_id_and_translation_key_from_name():
    ent, _ = _make(ENERGY)
    assert ent.unique_id == "eveus_limit_energy"
    assert ent._attr_translation_key == "limit_energy"


def _make_threshold(data):
    updater = MagicMock()
    updater.available = True
    updater.data = data
    updater.send_command = AsyncMock(return_value=True)
    updater.config_entry = MagicMock()
    ent = number_mod.EveusUndervoltageThresholdNumber(
        updater, number_mod.UNDERVOLTAGE_THRESHOLD_NUMBER, device_number=1
    )
    ent.hass = MagicMock()
    ent.async_write_ha_state = MagicMock()
    return ent, updater


def test_undervoltage_threshold_reads_and_writes_ai_voltage():
    description = getattr(number_mod, "UNDERVOLTAGE_THRESHOLD_NUMBER", None)
    assert description is not None

    ent, updater = _make_threshold({"aiVoltage": 215, "minVoltage": 200})

    assert ent._read_device_value() == 215.0
    assert ent.native_min_value == 210  # minVoltage 200 + 10
    assert ent.native_max_value == 220
    assert ent.native_step == 1
    assert ent.mode == NumberMode.SLIDER

    asyncio.run(ent.async_set_native_value(218))

    updater.send_command.assert_awaited_once_with("aiVoltage", 218)


def test_undervoltage_threshold_min_tracks_minvoltage():
    # Lower Minimum voltage -> the threshold floor follows minVoltage + 10.
    ent, updater = _make_threshold({"aiVoltage": 195, "minVoltage": 180})
    assert ent.native_min_value == 190
    assert ent._read_device_value() == 195.0  # 195 is valid once floor drops to 190

    # A live change to minVoltage updates the bound on the next poll AND pushes
    # the new bound to HA even though the value is unchanged.
    ent.async_write_ha_state.reset_mock()
    updater.data = {"aiVoltage": 195, "minVoltage": 150}
    ent._handle_coordinator_update()
    assert ent.native_min_value == 160
    ent.async_write_ha_state.assert_called()


def test_undervoltage_threshold_falls_back_to_static_floor():
    # No minVoltage reported yet -> stay at the description's 210 floor.
    ent, _ = _make_threshold({"aiVoltage": 215})
    assert ent.native_min_value == 210


def test_undervoltage_threshold_min_never_crosses_max():
    # A nonsense high minVoltage must not invert the slider range.
    ent, _ = _make_threshold({"aiVoltage": 215, "minVoltage": 300})
    assert ent.native_min_value == 220  # capped at native_max_value
