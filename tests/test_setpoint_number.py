"""EveusSetpointNumber scaling, clamping, and command wiring."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.number import NumberMode
from homeassistant.core import State

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


def test_energy_read_rounds_to_three_decimals():
    # Charger reports float noise (56.00899...); HA should show 3 decimals.
    ent, updater = _make(
        EveusSetpointNumberDescription(
            key="limit_energy",
            name="Limit Energy",
            command="energyLimit",
            state_key="energyLimit",
            device_to_ha=1.0,
            ha_to_device=1000.0,
            native_min_value=0.0,
            native_max_value=100.0,
            native_step=1.0,
            native_unit_of_measurement="kWh",
            display_precision=3,
        )
    )
    updater.data = {"energyLimit": 56.008999}
    assert ent._read_device_value() == 56.009


def test_time_read_rounds_to_whole_minutes():
    ent, updater = _make(
        EveusSetpointNumberDescription(
            key="limit_time",
            name="Limit Time",
            command="timeLimit",
            state_key="timeLimit",
            device_to_ha=1 / 60,
            ha_to_device=60.0,
            native_min_value=0.0,
            native_max_value=1440.0,
            native_step=5.0,
            native_unit_of_measurement="min",
            display_precision=0,
        )
    )
    updater.data = {"timeLimit": 29198}      # 486.633... min
    assert ent._read_device_value() == 487.0


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


def test_undervoltage_threshold_offlist_minvoltage_keeps_static_floor():
    # A nonsense/off-list minVoltage is not trusted to derive the floor; it
    # falls back to the safe static 210 V minimum rather than being capped.
    ent, _ = _make_threshold({"aiVoltage": 215, "minVoltage": 300})
    assert ent.native_min_value == 210


def test_threshold_write_reclamps_against_min_raised_while_queued():
    # F4: a write queued behind the command lock must clamp against the floor as
    # it is when the command is actually sent, not the floor captured at enqueue.
    ent, updater = _make_threshold({"aiVoltage": 215, "minVoltage": 150})  # floor 160

    async def scenario():
        await ent._command_lock.acquire()
        task = asyncio.ensure_future(ent.async_set_native_value(165))  # valid at 160
        await asyncio.sleep(0)  # let the write block on the lock
        # Minimum voltage rises while the write waits -> floor becomes 210.
        updater.data = {"aiVoltage": 215, "minVoltage": 200}
        ent._command_lock.release()
        await task
        # Must have re-clamped to the NEW floor, not sent the stale 165.
        updater.send_command.assert_awaited_once_with("aiVoltage", 210)

    asyncio.run(scenario())


def test_undervoltage_threshold_accepts_value_below_write_floor():
    # Real charger payload: minVoltage=200 (write floor 210) but a stored
    # aiVoltage=190 below it. The value must still be ACCEPTED and displayed —
    # only the slider/write range is gated on minVoltage+10.
    ent, _ = _make_threshold({"aiVoltage": 190, "minVoltage": 200})
    assert ent.native_min_value == 210          # write floor follows minVoltage+10
    assert ent._read_device_value() == 190.0    # but the reported value is accepted
    assert ent.native_value == 190.0


# ─── EveusSetpointNumber: property, boundaries, pending mid-flight ──────────


def test_setpoint_number_init_resolves_native_value_from_device_data():
    ent, _ = _make(ENERGY)  # updater.data has {"energyLimit": 0} by default
    assert ent.native_value is not None


def test_setpoint_number_state_key_is_a_string_not_a_bound_method():
    ent, _ = _make(ENERGY)
    assert ent._state_key == "energyLimit"


def test_setpoint_number_read_device_value_requires_available_and_data():
    ent, updater = _make(ENERGY)
    updater.available = True
    updater.data = None
    assert ent._read_device_value() is None


def test_setpoint_number_read_device_value_range_boundaries():
    ent, updater = _make(ENERGY)  # min=0, max=100
    updater.data = {"energyLimit": 100.0}
    assert ent._read_device_value() == 100.0
    updater.data = {"energyLimit": 100.1}
    assert ent._read_device_value() is None
    updater.data = {"energyLimit": 0.0}
    assert ent._read_device_value() == 0.0


@pytest.mark.parametrize(
    ("native_step", "optimistic", "device", "expected"),
    [
        (10, 100.0, 100.0, True),    # diff 0 -> always equal
        (10, 100.0, 95.0, False),    # diff == step/2 boundary: strictly NOT confirmed
        (10, 100.0, 90.0, False),    # diff 10: would wrongly pass under a step*2/step widening
        (10, 100.0, 96.0, True),     # diff 4: would wrongly fail under a step/3 narrowing
        (0, 100.0, 99.8, True),      # zero-step fallback threshold is 0.5, diff 0.2 confirms
        (0, 100.0, 99.0, False),     # diff 1.0 must NOT confirm under the 0.5 fallback
    ],
)
def test_setpoint_number_values_equal_matrix(native_step, optimistic, device, expected):
    ent, _ = _make(ENERGY)
    ent._attr_native_step = native_step
    assert ent._values_equal(optimistic, device) is expected


def test_setpoint_number_set_display_value_stores_it():
    ent, _ = _make(ENERGY)
    ent._set_display_value(42.0)
    assert ent._attr_native_value == 42.0


def test_setpoint_number_resolve_value_grace_period_boundaries():
    from custom_components.eveus.const import CONTROL_GRACE_PERIOD
    import time

    ent, updater = _make(ENERGY)
    updater.available = False
    updater.data = {}
    ent._last_device_value = 42.0
    ent._last_successful_read = time.time()
    assert ent._resolve_value() == 42.0  # age ~0, within grace

    # Grace expired -> must not return the stale value.
    ent._last_successful_read = time.time() - CONTROL_GRACE_PERIOD
    assert ent._resolve_value() is None


def test_setpoint_number_resolve_value_grace_boundary_exact(monkeypatch):
    """Pin the clock so age lands exactly on 0 and exactly on CONTROL_GRACE_PERIOD —
    wall-clock timing can't reliably hit these exact boundaries."""
    from custom_components.eveus.const import CONTROL_GRACE_PERIOD

    ent, updater = _make(ENERGY)
    updater.available = True
    updater.data = {}  # no energyLimit key -> device read returns None
    ent._last_device_value = 7.0
    ent._last_successful_read = 1000.0

    monkeypatch.setattr(number_mod.time, "time", lambda: 1000.0)  # age == 0 exactly
    assert ent._resolve_value() == 7.0

    monkeypatch.setattr(
        number_mod.time, "time", lambda: 1000.0 + CONTROL_GRACE_PERIOD
    )  # age == GRACE exactly -> must NOT be treated as still fresh
    assert ent._resolve_value() is None


def test_setpoint_number_resolve_value_ignores_stale_value_when_grace_expired_but_present():
    """A present last_device_value outside the grace window must fall through to
    None, not be returned unconditionally (guards the `and`/`or` and `is None`
    inversions on the fallback guard)."""
    import time
    from custom_components.eveus.const import CONTROL_GRACE_PERIOD

    ent, updater = _make(ENERGY)
    updater.available = True
    updater.data = {}  # no energyLimit key -> device read returns None
    ent._last_device_value = 5.0
    ent._last_successful_read = time.time() - CONTROL_GRACE_PERIOD - 10
    assert ent._resolve_value() is None


def test_setpoint_number_pending_value_visible_mid_command():
    ent, updater = _make(ENERGY)
    seen = {}

    async def _capture(command, value):
        seen["pending"] = ent._pending_value
        seen["attr"] = ent._attr_native_value
        return True

    updater.send_command = _capture
    asyncio.run(ent.async_set_native_value(40))
    assert seen["pending"] == 40.0
    assert seen["attr"] == 40.0


def test_setpoint_number_pending_cleared_after_command_completes():
    ent, _ = _make(ENERGY)
    asyncio.run(ent.async_set_native_value(40))
    assert ent._get_pending() is None  # not "" or any other non-None sentinel


def test_setpoint_number_native_value_repopulated_after_command():
    ent, _ = _make(ENERGY)
    asyncio.run(ent.async_set_native_value(40))
    assert ent.native_value is not None


def test_setpoint_number_restore_ignores_unknown_and_unavailable_silently():
    import logging

    ent, _ = _make(ENERGY)
    logger = logging.getLogger("custom_components.eveus.number")
    records = []
    handler = logging.Handler()
    handler.emit = lambda record: records.append(record)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        asyncio.run(ent._async_restore_state(State("number.x", "unknown")))
        asyncio.run(ent._async_restore_state(State("number.x", "unavailable")))
    finally:
        logger.removeHandler(handler)
    assert ent._last_device_value is None
    assert not any("Could not restore" in r.getMessage() for r in records)


def test_setpoint_number_restore_does_not_crash_on_none_state():
    ent, _ = _make(ENERGY)
    asyncio.run(ent._async_restore_state(None))


def test_setpoint_number_restore_range_boundaries_and_populates_native_value():
    ent, _ = _make(ENERGY)  # min=0, max=100
    asyncio.run(ent._async_restore_state(State("number.x", "0")))
    assert ent._last_device_value == 0.0
    asyncio.run(ent._async_restore_state(State("number.x", "100")))
    assert ent._last_device_value == 100.0
    assert ent.native_value == 100.0
    ent._last_device_value = None
    asyncio.run(ent._async_restore_state(State("number.x", "100.1")))
    assert ent._last_device_value is None


def test_setpoint_number_restore_records_a_real_read_timestamp():
    """A successful restore must stamp `_last_successful_read` with the real
    clock, not leave it None -- a None here would make the entity look like
    it has never had a successful read, defeating grace-period bookkeeping."""
    import time

    ent, _ = _make(ENERGY)
    ent._last_successful_read = None
    before = time.time()
    asyncio.run(ent._async_restore_state(State("number.x", "50")))
    assert ent._last_successful_read is not None
    assert ent._last_successful_read >= before


# ─── EveusUndervoltageThresholdNumber: boundaries, multiply, restore ────────


def test_undervoltage_threshold_read_min_boundary_is_zero_not_one():
    ent, updater = _make_threshold({"aiVoltage": 0.5, "minVoltage": 200})
    assert ent._read_device_value() == 0.5  # accepted even far below the write floor


def test_undervoltage_threshold_read_min_exact_boundary_is_inclusive():
    """Exactly at _READ_MIN (0.0) must be accepted (`<=`), not rejected (`<`)."""
    ent, updater = _make_threshold({"aiVoltage": 0, "minVoltage": 200})
    assert ent._read_device_value() == 0.0


def test_undervoltage_threshold_read_device_value_requires_available_and_data():
    ent, updater = _make_threshold({"aiVoltage": 215})
    updater.available = True
    updater.data = None
    assert ent._read_device_value() is None


def test_undervoltage_threshold_read_device_value_max_boundary():
    ent, updater = _make_threshold({"aiVoltage": 220})
    assert ent._read_device_value() == 220.0
    updater.data = {"aiVoltage": 220.5}
    assert ent._read_device_value() is None


def test_undervoltage_threshold_multiplies_not_divides_by_device_to_ha():
    from custom_components.eveus.number import EveusSetpointNumberDescription

    description = EveusSetpointNumberDescription(
        key="undervoltage_threshold",
        name="Undervoltage threshold",
        command="aiVoltage",
        state_key="aiVoltage",
        device_to_ha=2.0,  # distinguishes * from / (both are no-ops at 1.0)
        native_min_value=0,
        native_max_value=1000,
        native_step=1,
    )
    ent, updater = _make_threshold({"aiVoltage": 100, "minVoltage": 200})
    ent.entity_description = description
    ent._device_to_ha = description.device_to_ha
    ent._attr_native_max_value = description.native_max_value
    assert ent._read_device_value() == 200.0  # 100 * 2.0, not 100 / 2.0 == 50


def test_undervoltage_threshold_restore_ignores_unknown_and_unavailable_silently():
    import logging

    ent, _ = _make_threshold({"aiVoltage": 215})
    logger = logging.getLogger("custom_components.eveus.number")
    records = []
    handler = logging.Handler()
    handler.emit = lambda record: records.append(record)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        asyncio.run(ent._async_restore_state(State("number.x", "unknown")))
        asyncio.run(ent._async_restore_state(State("number.x", "unavailable")))
    finally:
        logger.removeHandler(handler)
    assert ent._last_device_value is None
    assert not any("Could not restore" in r.getMessage() for r in records)


def test_undervoltage_threshold_restore_does_not_crash_on_none_state():
    ent, _ = _make_threshold({"aiVoltage": 215})
    asyncio.run(ent._async_restore_state(None))


def test_undervoltage_threshold_restore_range_boundaries_and_populates_native_value():
    ent, _ = _make_threshold({})  # READ_MIN=0.0, max=220
    asyncio.run(ent._async_restore_state(State("number.x", "0")))
    assert ent._last_device_value == 0.0
    asyncio.run(ent._async_restore_state(State("number.x", "220")))
    assert ent._last_device_value == 220.0
    assert ent.native_value == 220.0
    ent._last_device_value = None
    asyncio.run(ent._async_restore_state(State("number.x", "220.5")))
    assert ent._last_device_value is None


def test_undervoltage_threshold_restore_records_a_real_read_timestamp():
    import time

    ent, _ = _make_threshold({})
    ent._last_successful_read = None
    before = time.time()
    asyncio.run(ent._async_restore_state(State("number.x", "150")))
    assert ent._last_successful_read is not None
    assert ent._last_successful_read >= before


def test_undervoltage_threshold_refresh_min_bound_requires_available_and_data():
    ent, updater = _make_threshold({"aiVoltage": 215, "minVoltage": 150})
    assert ent.native_min_value == 160
    updater.available = False
    ent._refresh_min_bound()
    assert ent.native_min_value == ent._floor_min_value  # falls back, ignores stale data


def test_undervoltage_threshold_write_state_guard_only_writes_on_real_change():
    ent, updater = _make_threshold({"aiVoltage": 215, "minVoltage": 200})  # floor 210
    ent._handle_coordinator_update()  # warm up _last_written_value/_last_written_available
    ent.async_write_ha_state.reset_mock()
    updater.data = {"aiVoltage": 215, "minVoltage": 200}  # unchanged
    ent._handle_coordinator_update()
    ent.async_write_ha_state.assert_not_called()
