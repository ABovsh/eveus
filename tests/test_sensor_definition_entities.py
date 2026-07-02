"""Unit tests for optimized sensor entity behavior."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from homeassistant.helpers.entity import EntityCategory

from conftest import TEST_HOST
from custom_components.eveus.sensor_definitions import (
    OptimizedEveusSensor,
    SensorSpec,
    SensorType,
    get_charger_substate,
    get_connection_attrs,
    get_connection_quality,
    get_ground_status,
    get_session_time,
    get_session_time_attrs,
    get_time_drift,
)


class _Updater:
    host = TEST_HOST
    available = True
    last_update_success = True
    data = {"value": "10"}
    connection_quality = {
        "success_rate": 75,
        "latency_avg": 0.42,
    }

    def async_add_listener(self, *args: object, **kwargs: object):
        return lambda: None


def _sensor(value_fn, *, sensor_type: SensorType = SensorType.MEASUREMENT):
    spec = SensorSpec(
        key="test_sensor",
        name="Test Sensor",
        value_fn=value_fn,
        sensor_type=sensor_type,
        icon="mdi:test-tube",
        device_class="power",
        state_class="measurement",
        unit="W",
        precision=1,
        category=EntityCategory.DIAGNOSTIC,
        attributes_fn=lambda updater, hass: {"ok": True},
    )
    entity = OptimizedEveusSensor(_Updater(), spec)
    entity.hass = SimpleNamespace(config=SimpleNamespace(time_zone="Europe/Kiev"))
    entity.async_write_ha_state = lambda: None
    return entity


def test_optimized_sensor_applies_description_fields() -> None:
    entity = _sensor(lambda updater, hass: 10)

    assert entity.icon == "mdi:test-tube"
    assert entity.device_class == "power"
    assert entity.state_class == "measurement"
    assert entity.native_unit_of_measurement == "W"
    assert entity.suggested_display_precision == 1
    assert entity.entity_category == EntityCategory.DIAGNOSTIC
    entity._handle_coordinator_update()
    assert entity.extra_state_attributes == {"ok": True}


def test_optimized_sensor_uses_fresh_coordinator_data() -> None:
    calls = 0

    def value_fn(updater, hass):
        nonlocal calls
        calls += 1
        return calls

    entity = _sensor(value_fn)

    entity._handle_coordinator_update()
    assert entity.native_value == 1
    assert entity.native_value == 1
    assert calls == 1

    entity._handle_coordinator_update()
    assert entity.native_value == 2
    assert calls == 2


def test_optimized_sensor_recalculates_calculated_values() -> None:
    calls = 0

    def value_fn(updater, hass):
        nonlocal calls
        calls += 1
        return calls

    entity = _sensor(value_fn, sensor_type=SensorType.CALCULATED)

    entity._handle_coordinator_update()
    assert entity.native_value == 1
    entity._handle_coordinator_update()
    assert entity.native_value == 2


def test_optimized_sensor_returns_none_when_offline() -> None:
    updater = _Updater()
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="value",
            name="Value",
            value_fn=lambda updater, hass: float(updater.data["value"]),
            sensor_type=SensorType.MEASUREMENT,
        ),
    )
    entity.hass = object()
    entity.async_write_ha_state = lambda: None

    entity._handle_coordinator_update()
    assert entity.native_value == 10
    updater.available = False
    entity._unavailable_since = 0
    entity._handle_coordinator_update()
    assert entity.native_value is None


def test_optimized_sensor_error_paths_are_rate_limited() -> None:
    entity = _sensor(lambda updater, hass: (_ for _ in ()).throw(ValueError("boom")))

    assert entity.native_value is None
    assert entity.native_value is None


def test_session_ground_time_drift_and_connection_helpers() -> None:
    hass = SimpleNamespace(config=SimpleNamespace(time_zone="Europe/Kiev"))
    updater = SimpleNamespace(
        available=True,
        data={
            "sessionTime": "3660",
            "ground": "0",
            "systemTime": "1714300000",
            "timeZone": "3",
        },
        connection_quality={"success_rate": 75, "latency_avg": 0.42},
    )

    assert get_session_time(updater, hass) == "1h 01m"
    assert get_session_time_attrs(updater, hass) == {"duration_seconds": 3660}
    assert get_ground_status(updater, hass) == "Not Connected"
    assert isinstance(get_time_drift(updater, hass), int)
    assert get_connection_quality(updater, hass) == 75
    assert get_connection_attrs(updater, hass)["status"] == "Fair"


def test_connection_helpers_handle_errors() -> None:
    updater = SimpleNamespace(available=True)

    assert get_connection_quality(updater, None) is None
    assert get_connection_attrs(updater, None) == {"status": "Error"}


def test_substate_returns_none_for_unknown_state() -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus import sensor_definitions as sd

    updater = EveusTestUpdater({"state": 99, "subState": 1})
    assert sd.get_charger_substate(updater, None) is None


def test_all_sensor_specs_have_valid_device_and_state_class_pairs() -> None:
    from homeassistant.components.sensor.const import DEVICE_CLASS_STATE_CLASSES
    from custom_components.eveus.sensor_definitions import create_sensor_specifications

    for phases in (1, 3):
        for spec in create_sensor_specifications(phases=phases):
            if spec.device_class is None or spec.state_class is None:
                continue
            allowed = DEVICE_CLASS_STATE_CLASSES.get(spec.device_class)
            if allowed is None:
                continue
            assert spec.state_class in allowed, (
                f"{spec.key}: state_class {spec.state_class!r} is invalid for "
                f"device_class {spec.device_class!r} (allowed: {allowed})"
            )


def test_soc_energy_uses_energy_storage_device_class() -> None:
    from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
    from homeassistant.components.sensor.const import DEVICE_CLASS_STATE_CLASSES
    from conftest import EveusTestUpdater
    from custom_components.eveus.ev_sensors import CachedSOCCalculator, EVSocKwhSensor

    entity = EVSocKwhSensor(EveusTestUpdater(data={}), 1, CachedSOCCalculator())
    assert entity.device_class == SensorDeviceClass.ENERGY_STORAGE
    assert entity.state_class == SensorStateClass.MEASUREMENT
    allowed = DEVICE_CLASS_STATE_CLASSES.get(SensorDeviceClass.ENERGY_STORAGE)
    assert entity.state_class in allowed


from datetime import datetime, timedelta as _td, timezone as _tz


class _Clock:
    """Deterministic clock; advances one minute per call."""

    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=_tz.utc)

    def utcnow(self) -> datetime:
        self.now += _td(minutes=1)
        return self.now


def _make_cost_sensor(updater):
    from custom_components.eveus.sensor_definitions import create_sensor_specifications
    from conftest import disable_state_writes

    for spec in create_sensor_specifications():
        if spec.key == "session_cost":
            entity = spec.create_sensor(updater, 1)
            disable_state_writes(entity)
            entity.hass = None
            return entity
    raise AssertionError("session_cost spec not found")


def test_cost_sensor_sets_last_reset_on_first_value(monkeypatch) -> None:
    import custom_components.eveus.sensor_definitions as sd
    from conftest import EveusTestUpdater

    monkeypatch.setattr(sd.dt_util, "utcnow", _Clock().utcnow)
    updater = EveusTestUpdater(data={"sessionMoney": 4.32})
    entity = _make_cost_sensor(updater)

    entity._update_native_value()

    assert entity.native_value == 4.32
    assert entity.last_reset is not None


def test_cost_sensor_keeps_last_reset_while_value_rises(monkeypatch) -> None:
    import custom_components.eveus.sensor_definitions as sd
    from conftest import EveusTestUpdater

    monkeypatch.setattr(sd.dt_util, "utcnow", _Clock().utcnow)
    updater = EveusTestUpdater(data={"sessionMoney": 4.32})
    entity = _make_cost_sensor(updater)
    entity._update_native_value()
    first_reset = entity.last_reset

    updater.data = {"sessionMoney": 6.50}
    entity._update_native_value()

    assert entity.native_value == 6.50
    assert entity.last_reset == first_reset


def test_cost_sensor_advances_last_reset_when_meter_resets(monkeypatch) -> None:
    import custom_components.eveus.sensor_definitions as sd
    from conftest import EveusTestUpdater

    monkeypatch.setattr(sd.dt_util, "utcnow", _Clock().utcnow)
    updater = EveusTestUpdater(data={"sessionMoney": 6.50})
    entity = _make_cost_sensor(updater)
    entity._update_native_value()
    first_reset = entity.last_reset

    updater.data = {"sessionMoney": 1.10}
    entity._update_native_value()

    assert entity.native_value == 1.10
    assert entity.last_reset is not None
    assert entity.last_reset > first_reset


def test_cost_sensor_does_not_treat_offline_gap_as_reset(monkeypatch) -> None:
    import custom_components.eveus.sensor_definitions as sd
    from conftest import EveusTestUpdater

    monkeypatch.setattr(sd.dt_util, "utcnow", _Clock().utcnow)
    updater = EveusTestUpdater(data={"sessionMoney": 4.00})
    entity = _make_cost_sensor(updater)
    entity._update_native_value()
    first_reset = entity.last_reset

    updater.data = {}
    entity._update_native_value()
    updater.data = {"sessionMoney": 4.80}
    entity._update_native_value()

    assert entity.last_reset == first_reset


def test_connection_quality_not_healthy_before_first_success() -> None:
    from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
    from custom_components.eveus.common_network import EveusUpdater

    class _Hass:
        loop = None

    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    assert updater.connection_quality["is_healthy"] is False


def test_connection_quality_healthy_after_success() -> None:
    from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
    from custom_components.eveus.common_network import EveusUpdater

    class _Hass:
        loop = None

    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    updater._record_success(0.1, {"state": 2, "currentSet": 16})
    assert updater.connection_quality["is_healthy"] is True


def test_updater_exposes_basic_auth_accessor() -> None:
    from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
    from custom_components.eveus.common_network import EveusUpdater

    class _Hass:
        loop = None

    updater = EveusUpdater(TEST_HOST, TEST_USERNAME, TEST_PASSWORD, _Hass())
    assert updater.basic_auth is updater._basic_auth


def test_wifi_rssi_accepts_typical_range() -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus import sensor_definitions as sd

    assert sd.get_wifi_rssi(EveusTestUpdater({"RSSI": -55}), None) == -55


@pytest.mark.parametrize("bad", [10, 50, 100])
def test_wifi_rssi_rejects_positive_values(bad: int) -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus import sensor_definitions as sd

    assert sd.get_wifi_rssi(EveusTestUpdater({"RSSI": bad}), None) is None


@pytest.mark.parametrize("key", ["counter_a_cost", "counter_b_cost", "session_cost"])
def test_cost_sensors_are_monetary_iso(key: str) -> None:
    from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
    from custom_components.eveus.sensor_definitions import get_sensor_specifications

    by_key = {s.key: s for s in get_sensor_specifications(1)}
    spec = by_key[key]
    assert spec.device_class == SensorDeviceClass.MONETARY
    assert spec.unit == "UAH"
    assert spec.state_class == SensorStateClass.TOTAL


def test_session_cost_is_monetary_total() -> None:
    from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
    from custom_components.eveus.sensor_definitions import get_sensor_specifications

    by_key = {s.key: s for s in get_sensor_specifications(1)}
    assert by_key["session_cost"].state_class == SensorStateClass.TOTAL
    assert by_key["session_cost"].device_class == SensorDeviceClass.MONETARY


def test_negative_session_time_reads_unknown() -> None:
    from conftest import EveusTestUpdater

    updater = EveusTestUpdater(data={"sessionTime": -1})
    assert get_session_time(updater, None) is None
    assert get_session_time_attrs(updater, None) == {}


def test_valid_session_time_still_renders() -> None:
    from conftest import EveusTestUpdater

    updater = EveusTestUpdater(data={"sessionTime": 3661})
    assert get_session_time(updater, None) == "1h 01m"
    assert get_session_time_attrs(updater, None) == {"duration_seconds": 3661}


@pytest.mark.parametrize(
    "getter,key",
    [
        ("get_session_energy", "sessionEnergy"),
        ("get_total_energy", "totalEnergy"),
        ("get_counter_a_energy", "IEM1"),
        ("get_counter_a_cost", "IEM1_money"),
        ("get_counter_b_energy", "IEM2"),
        ("get_counter_b_cost", "IEM2_money"),
        ("get_session_cost", "sessionMoney"),
    ],
)
def test_energy_cost_getters_reject_finite_outliers(getter, key: str) -> None:
    import custom_components.eveus.sensor_definitions as sd
    from conftest import EveusTestUpdater

    fn = getattr(sd, getter)
    assert fn(EveusTestUpdater(data={key: 1e100}), None) is None
    assert fn(EveusTestUpdater(data={key: 42.5}), None) is not None


def test_schedule_current_limit_dropped_when_above_model_max() -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus import sensor_definitions as sd

    updater = EveusTestUpdater(
        {
            "sh1Start": 60,
            "sh1Stop": 120,
            "sh1CurrentEnable": 1,
            "sh1CurrentValue": 999,
        }
    )
    attrs = sd._make_schedule_attrs(1)(updater, None)
    assert "current_limit_a" not in attrs


def test_schedule_energy_limit_drops_outlier() -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus import sensor_definitions as sd

    attrs_fn = sd._make_schedule_attrs(1)
    updater = EveusTestUpdater(
        {"sh1EnergyEnable": 1, "sh1EnergyValue": 1_000_000_000}
    )
    assert "energy_limit_kwh" not in attrs_fn(updater, None)


def test_schedule_energy_limit_keeps_reasonable_value() -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus import sensor_definitions as sd

    attrs_fn = sd._make_schedule_attrs(1)
    updater = EveusTestUpdater({"sh1EnergyEnable": 1, "sh1EnergyValue": 50})
    assert attrs_fn(updater, None)["energy_limit_kwh"] == 50


def test_adaptive_current_limit_bounded_to_model_max() -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus.sensor_definitions import create_sensor_specifications

    spec = next(s for s in create_sensor_specifications(max_current=16) if s.key == "adaptive_current_limit")
    assert spec.value_fn(EveusTestUpdater(data={"aiModecurrent": 48}), None) is None
    assert spec.value_fn(EveusTestUpdater(data={"aiModecurrent": 10}), None) == 10


def test_schedule_current_limit_bounded_to_model_max() -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus.sensor_definitions import create_sensor_specifications

    spec = next(s for s in create_sensor_specifications(max_current=16) if s.key == "schedule_1")
    bad = spec.attributes_fn(
        EveusTestUpdater(data={"sh1CurrentEnable": 1, "sh1CurrentValue": 48}), None
    )
    assert "current_limit_a" not in bad
    ok = spec.attributes_fn(
        EveusTestUpdater(data={"sh1CurrentEnable": 1, "sh1CurrentValue": 12}), None
    )
    assert ok["current_limit_a"] == 12


def test_session_time_attrs_reject_absurd_duration() -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus.const import MAX_SESSION_TIME_SECONDS
    from custom_components.eveus.sensor_definitions import get_session_time_attrs

    updater = EveusTestUpdater({"sessionTime": MAX_SESSION_TIME_SECONDS + 1})
    assert get_session_time_attrs(updater, None) == {}

    updater = EveusTestUpdater({"sessionTime": 3600})
    assert get_session_time_attrs(updater, None) == {"duration_seconds": 3600}


def test_active_rate_cost_rejects_negative_tariff() -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus import sensor_definitions as sd

    updater = EveusTestUpdater({"activeTarif": 0, "tarif": -100})
    assert sd.get_active_rate_cost(updater, None) is None


def test_active_rate_cost_returns_value_when_positive() -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus import sensor_definitions as sd

    updater = EveusTestUpdater({"activeTarif": 1, "tarifAValue": 250})
    assert sd.get_active_rate_cost(updater, None) == 2.5


def test_v12_error_state_zero_substate_is_unknown() -> None:
    upd = SimpleNamespace(available=True, data={"state": 7, "subState": 0})
    assert get_charger_substate(upd, None) is None


def test_v12_normal_state_zero_substate_still_maps() -> None:
    upd = SimpleNamespace(available=True, data={"state": 2, "subState": 0})
    assert get_charger_substate(upd, None) == "No Limits"


def test_v12_error_state_real_fault_still_maps() -> None:
    upd = SimpleNamespace(available=True, data={"state": 7, "subState": 10})
    assert get_charger_substate(upd, None) == "Overcurrent"


def test_v21_schedule_energy_has_display_precision() -> None:
    import custom_components.eveus.number as number_mod

    for desc in number_mod.SCHEDULE_LIMIT_NUMBERS:
        if desc.key.endswith("energy_limit"):
            assert desc.display_precision == 3


import asyncio as _asyncio


def _rc10_cost_sensor():
    from custom_components.eveus.sensor_definitions import create_sensor_specifications
    from conftest import EveusTestUpdater, disable_state_writes

    for spec in create_sensor_specifications():
        if spec.key == "session_cost":
            entity = spec.create_sensor(EveusTestUpdater(data={}), 1)
            disable_state_writes(entity)
            entity.hass = None
            return entity
    raise AssertionError("session_cost spec not found")


def test_restore_rejects_non_finite_prev_cost_value() -> None:
    from types import SimpleNamespace

    entity = _rc10_cost_sensor()
    state = SimpleNamespace(state="inf", attributes={})

    _asyncio.run(entity._async_restore_state(state))

    assert entity._prev_cost_value is None


def test_restore_accepts_finite_prev_cost_value() -> None:
    import pytest
    from types import SimpleNamespace

    entity = _rc10_cost_sensor()
    state = SimpleNamespace(state="4.32", attributes={})

    _asyncio.run(entity._async_restore_state(state))

    assert entity._prev_cost_value == pytest.approx(4.32)


def test_restore_ignores_non_datetime_last_reset() -> None:
    from types import SimpleNamespace

    entity = _rc10_cost_sensor()
    state = SimpleNamespace(state="4.32", attributes={"last_reset": 1234567890})

    _asyncio.run(entity._async_restore_state(state))

    assert entity._attr_last_reset is None


def test_restore_accepts_datetime_last_reset() -> None:
    from datetime import datetime, timezone as _tz
    from types import SimpleNamespace

    entity = _rc10_cost_sensor()
    reset = datetime(2026, 1, 1, tzinfo=_tz.utc)
    state = SimpleNamespace(state="4.32", attributes={"last_reset": reset})

    _asyncio.run(entity._async_restore_state(state))

    assert entity._attr_last_reset == reset


def test_session_active_unknown_in_error_state() -> None:
    from custom_components.eveus.binary_sensor import _session_active_is_on

    assert _session_active_is_on({"state": 7}) is None
    assert _session_active_is_on({"state": 4}) is True
    assert _session_active_is_on({"state": 2}) is False


def test_switch_rejects_out_of_domain_state_value() -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus.switch import SWITCH_DESCRIPTIONS, BaseSwitchEntity

    description = SWITCH_DESCRIPTIONS[0]  # Stop Charging / evseEnabled
    updater = EveusTestUpdater({"evseEnabled": 2})
    sw = BaseSwitchEntity(updater, description, 1)
    assert sw._resolve_state() is None


def test_soc_number_survives_corrupt_restore_value(monkeypatch) -> None:
    from conftest import EveusTestUpdater, HelperHass, disable_state_writes
    from custom_components.eveus.ev_sensors import CachedSOCCalculator
    from custom_components.eveus.number import EveusBatteryCapacityNumber
    from custom_components.eveus import number as number_module

    updater = EveusTestUpdater({})
    calc = CachedSOCCalculator()
    entity = EveusBatteryCapacityNumber(updater, calc, seed=50, device_number=1)
    entity.hass = HelperHass({})
    disable_state_writes(entity)
    monkeypatch.setattr(number_module, "async_dispatcher_send", lambda *a, **k: None)

    async def corrupt_last_number_data():
        return SimpleNamespace(native_value="garbage")

    entity.async_get_last_number_data = corrupt_last_number_data

    monkeypatch.setattr(
        type(entity).__mro__[1], "async_added_to_hass", lambda self: _asyncio.sleep(0)
    )
    _asyncio.run(entity.async_added_to_hass())
    assert entity.native_value == 50


def test_missing_or_corrupt_fields_leave_state_unchanged() -> None:
    from custom_components.eveus import _ClockDriftTracker
    import time

    def _p(drift, tz=3):
        return {"systemTime": int(time.time() + drift + tz * 3600), "timeZone": tz}

    tracker = _ClockDriftTracker()
    tracker.evaluate(_p(900))
    tracker.evaluate(_p(900))
    assert tracker.evaluate({}) is None
    assert tracker.evaluate({"systemTime": -5, "timeZone": 3}) is None
    assert tracker.evaluate({"systemTime": "x", "timeZone": 99}) is None
    # Streak survived the garbage: the next valid drifted poll fires.
    assert tracker.evaluate(_p(900)) is True


def test_is_device_number_taken_helper_exists() -> None:
    from custom_components.eveus import utils

    assert callable(utils.is_device_number_taken)


def test_eta_seconds_is_none_when_division_overflows() -> None:
    from custom_components.eveus.utils import calculate_remaining_seconds

    result = calculate_remaining_seconds(20, 80, 1e-310, 50, 0)
    assert result is None


def test_eta_string_is_unavailable_when_division_overflows() -> None:
    from custom_components.eveus.utils import calculate_remaining_time

    result = calculate_remaining_time(20, 80, 1e-310, 50, 0)
    assert result == "unavailable"


def test_eta_seconds_still_finite_for_normal_power() -> None:
    import math
    from custom_components.eveus.utils import calculate_remaining_seconds

    result = calculate_remaining_seconds(20, 80, 3000, 50, 0)
    assert result is not None and math.isfinite(result) and result > 0


def test_v08_tiny_power_returns_no_eta() -> None:
    from custom_components.eveus.utils import calculate_remaining_seconds

    assert (
        calculate_remaining_seconds(
            current_soc=50, target_soc=80, power_meas=1e-250,
            battery_capacity=50, correction=0,
        )
        is None
    )


def test_v08_normal_power_still_returns_eta() -> None:
    from custom_components.eveus.utils import calculate_remaining_seconds

    secs = calculate_remaining_seconds(
        current_soc=50, target_soc=80, power_meas=7000,
        battery_capacity=50, correction=0,
    )
    assert secs is not None and secs > 0


def test_v07_current_set_sensor_displays_sub_7() -> None:
    from custom_components.eveus import sensor_definitions as sd

    specs = {s.name: s for s in sd.create_sensor_specifications(phases=1, max_current=16)}
    getter = specs["Current Set"].value_fn
    upd = SimpleNamespace(available=True, data={"currentSet": 6})
    assert getter(upd, None) == 6


def test_v07_current_number_displays_sub7_but_writes_floor() -> None:
    import asyncio as _asyncio2
    from unittest.mock import AsyncMock, MagicMock
    from custom_components.eveus import number as number_mod

    upd = MagicMock()
    upd.available = True
    upd.data = {"currentSet": 6, "state": 4}
    upd.config_entry = MagicMock()
    upd.send_command = AsyncMock(return_value=True)
    num = number_mod.EveusCurrentNumber(upd, "16A")
    num.hass = MagicMock()
    num.async_write_ha_state = MagicMock()
    assert num.native_value == 6
    _asyncio2.run(num.async_set_native_value(3))
    upd.send_command.assert_awaited_with("currentSet", 7)


def test_v14_charger_number_prefers_fresh_device_over_restored(monkeypatch) -> None:
    import asyncio as _asyncio3
    import time as _t
    from unittest.mock import AsyncMock, MagicMock
    from custom_components.eveus import number as number_mod

    upd = MagicMock()
    upd.available = True
    upd.data = {"currentSet": 14, "state": 4}
    upd.config_entry = MagicMock()
    num = number_mod.EveusCurrentNumber(upd, "16A")
    num.hass = MagicMock()
    num.async_write_ha_state = MagicMock()
    num._last_device_value = 10.0
    num._last_successful_read = _t.time()
    num._attr_native_value = 10.0
    monkeypatch.setattr(
        "custom_components.eveus.common_base.BaseEveusEntity.async_added_to_hass",
        AsyncMock(),
    )
    _asyncio3.run(num.async_added_to_hass())
    assert num.native_value == 14


def test_v16_soc_stop_auth_failure_starts_reauth_and_withdraws_token() -> None:
    import asyncio as _asyncio4
    from unittest.mock import AsyncMock, MagicMock
    from homeassistant.exceptions import ConfigEntryAuthFailed
    from custom_components.eveus.ev_sensors import CachedSOCCalculator
    from custom_components.eveus.soc_limit import SocLimitController

    def _calc(target=80, initial=20, cap=50, corr=0):
        c = CachedSOCCalculator()
        c.set_value("initial_soc", initial)
        c.set_value("battery_capacity", cap)
        c.set_value("soc_correction", corr)
        c.set_value("target_soc", target)
        return c

    calc = _calc(target=80, initial=20, cap=50, corr=0)
    updater = MagicMock()
    updater.available = True
    updater.last_update_success = True
    updater.device_number = 1
    updater.data = {"state": 4, "sessionEnergy": 30.0, "evseEnabled": 0, "suspendLimits": 0}
    updater.send_command = AsyncMock(side_effect=ConfigEntryAuthFailed("401"))
    updater.config_entry = MagicMock()

    hass = MagicMock()
    hass.async_create_task = lambda coro: _asyncio4.run(coro)
    hass.bus.async_fire = MagicMock()

    ctrl = SocLimitController(hass, updater, calc)
    ctrl.set_enabled(True)
    ctrl.process()
    updater.config_entry.async_start_reauth.assert_called_once()
    assert ctrl._pending is None


def test_v21_schedule_energy_has_display_precision_alias() -> None:
    import custom_components.eveus.number as number_mod

    for desc in number_mod.SCHEDULE_LIMIT_NUMBERS:
        if desc.key.endswith("energy_limit"):
            assert desc.display_precision == 3


def test_v10_drift_clears_after_sync() -> None:
    import time
    from datetime import timedelta, timezone as _tz
    from homeassistant.util import dt as dt_util

    original = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(_tz(timedelta(hours=3)))
    try:
        shift = 3 * 3600
        updater = SimpleNamespace(
            available=True,
            data={"timeZone": "3", "systemTime": str(int(time.time()) + shift + 30)},
        )
        assert get_time_drift(updater, None) == 30
        updater.data["systemTime"] = str(int(time.time()) + shift)
        assert get_time_drift(updater, None) == 0
    finally:
        dt_util.set_default_time_zone(original)


def test_schedule_attrs_drop_invalid_current_and_energy() -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus import sensor_definitions as sd

    updater = EveusTestUpdater(
        {
            "sh1Start": 60,
            "sh1Stop": 120,
            "sh1CurrentEnable": 1,
            "sh1CurrentValue": -5,
            "sh1EnergyEnable": 1,
            "sh1EnergyValue": -1.0,
        }
    )
    attrs = sd._make_schedule_attrs(1)(updater, None)
    assert "current_limit_a" not in attrs
    assert "energy_limit_kwh" not in attrs


def test_charging_current_hides_out_of_range_device_value() -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus import number as number_mod

    updater = EveusTestUpdater({"currentSet": 48})  # 16A model max
    num = number_mod.EveusCurrentNumber(updater, "16A", 1)
    assert num._resolve_value() is None


def test_time_drift_rejects_negative_clock() -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus import sensor_definitions as sd

    assert sd.get_time_drift(
        EveusTestUpdater({"systemTime": -1, "timeZone": 3}), None
    ) is None


def test_time_drift_rejects_far_future_clock() -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus import sensor_definitions as sd

    assert sd.get_time_drift(
        EveusTestUpdater({"systemTime": 99999999999, "timeZone": 3}), None
    ) is None


def test_session_time_rejects_absurd_duration() -> None:
    from conftest import EveusTestUpdater
    from custom_components.eveus.sensor_definitions import get_session_time

    assert get_session_time(EveusTestUpdater(data={"sessionTime": 10**12}), None) is None
    assert get_session_time(EveusTestUpdater(data={"sessionTime": 3600}), None) == "1h 00m"


def test_current_set_sensor_rejects_value_above_model_maximum() -> None:
    from conftest import EveusTestUpdater, disable_state_writes
    from custom_components.eveus.sensor_definitions import create_sensor_specifications

    spec = next(s for s in create_sensor_specifications(phases=1, max_current=16) if s.key == "current_set")
    updater = EveusTestUpdater(data={"currentSet": 40})  # impossible on a 16 A unit
    entity = spec.create_sensor(updater, 1)
    disable_state_writes(entity)
    entity.hass = None

    assert entity._get_sensor_value() is None


def test_current_set_sensor_accepts_value_within_model_maximum() -> None:
    from conftest import EveusTestUpdater, disable_state_writes
    from custom_components.eveus.sensor_definitions import create_sensor_specifications

    spec = next(s for s in create_sensor_specifications(phases=1, max_current=16) if s.key == "current_set")
    updater = EveusTestUpdater(data={"currentSet": 14})
    entity = spec.create_sensor(updater, 1)
    disable_state_writes(entity)
    entity.hass = None

    assert entity._get_sensor_value() == 14


def test_cost_sensors_use_monetary_iso_unit() -> None:
    from homeassistant.components.sensor import SensorDeviceClass
    from custom_components.eveus.sensor_definitions import get_sensor_specifications

    by_key = {s.key: s for s in get_sensor_specifications(1)}
    for key in ("counter_a_cost", "counter_b_cost", "session_cost"):
        assert by_key[key].unit == "UAH"
        assert by_key[key].device_class == SensorDeviceClass.MONETARY
