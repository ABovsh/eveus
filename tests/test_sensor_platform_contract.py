"""Spec-driven sensors as the sensor platform hands them to Home Assistant.

Entities come from ``sensor.async_setup_entry`` with the spec cache cleared
first: ``get_sensor_specifications`` is ``lru_cache``d, so without the clear a
test reads a tuple another test built and never runs the spec factory at all.
Assertions are on what HA reads from the entity: unique_id, device/state class,
unit, display precision, category, state value and attributes, ``last_reset``
and the extra data HA saves for the next restart.
"""
from __future__ import annotations

import asyncio
import copy
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from homeassistant.helpers.restore_state import RestoredExtraData

from conftest import EveusTestUpdater, disable_state_writes
from custom_components.eveus import sensor as sensor_platform
from custom_components.eveus import sensor_definitions

# unique_id -> (device class, state class, unit, display precision, category)
_ONE_PHASE_SENSORS = {
    "eveus_voltage": ("voltage", "measurement", "V", 0, None),
    "eveus_current": ("current", "measurement", "A", 1, None),
    "eveus_power": ("power", "measurement", "W", 1, None),
    "eveus_current_set": ("current", None, "A", 0, "diagnostic"),
    "eveus_session_energy": (None, "measurement", "kWh", 2, None),
    "eveus_total_energy": ("energy", "total_increasing", "kWh", 2, None),
    "eveus_counter_a_energy": ("energy", "total_increasing", "kWh", 2, None),
    "eveus_counter_b_energy": ("energy", "total_increasing", "kWh", 2, None),
    "eveus_state": ("enum", None, None, None, "diagnostic"),
    "eveus_substate": ("enum", None, None, None, "diagnostic"),
    "eveus_not_charging_reason": ("enum", None, None, None, "diagnostic"),
    "eveus_ground": ("enum", None, None, None, "diagnostic"),
    "eveus_time_drift": (None, None, "s", None, "diagnostic"),
    "eveus_box_temperature": ("temperature", "measurement", "°C", 0, "diagnostic"),
    "eveus_plug_temperature": ("temperature", "measurement", "°C", 0, "diagnostic"),
    "eveus_battery_voltage": ("voltage", "measurement", "V", 2, "diagnostic"),
    "eveus_leakage_current": ("current", None, "mA", 0, "diagnostic"),
    "eveus_leakage_current_peak": ("current", None, "mA", 0, "diagnostic"),
    "eveus_wifi_signal": ("signal_strength", None, "dBm", 0, "diagnostic"),
    "eveus_session_time": (None, None, None, None, None),
    "eveus_counter_a_cost": ("monetary", "total", "UAH", 2, None),
    "eveus_counter_b_cost": ("monetary", "total", "UAH", 2, None),
    "eveus_primary_rate_cost": (None, None, "₴/kWh", 2, None),
    "eveus_active_rate_cost": (None, None, "₴/kWh", 2, None),
    "eveus_rate_2_cost": (None, None, "₴/kWh", 2, None),
    "eveus_rate_3_cost": (None, None, "₴/kWh", 2, None),
    "eveus_rate_2_status": ("enum", None, None, None, None),
    "eveus_rate_3_status": ("enum", None, None, None, None),
    "eveus_session_cost": ("monetary", "total", "UAH", 2, None),
    "eveus_adaptive_charging": ("enum", None, None, None, "diagnostic"),
    "eveus_adaptive_current_limit": ("current", None, "A", 0, "diagnostic"),
    "eveus_schedule_1": ("enum", None, None, None, "diagnostic"),
    "eveus_schedule_2": ("enum", None, None, None, "diagnostic"),
    "eveus_connection_quality": (None, None, "%", 0, "diagnostic"),
}

_THREE_PHASE_EXTRA = {
    "eveus_current_phase_2": ("current", "measurement", "A", 1, None),
    "eveus_current_phase_3": ("current", "measurement", "A", 1, None),
    "eveus_voltage_phase_2": ("voltage", "measurement", "V", 0, None),
    "eveus_voltage_phase_3": ("voltage", "measurement", "V", 0, None),
}


def _set_up_sensors(data: dict | None = None, *, phases: int = 1) -> dict:
    sensor_definitions.get_sensor_specifications.cache_clear()
    updater = EveusTestUpdater(copy.deepcopy(data) if data is not None else {})
    entry = SimpleNamespace(
        entry_id="entry-id",
        data={"soc_mode": "basic"},
        runtime_data=SimpleNamespace(
            updater=updater, device_number=1, soc_calculator=None, phases=phases
        ),
    )
    added: list = []
    asyncio.run(
        sensor_platform.async_setup_entry(
            object(), entry, lambda entities, update_before_add=False: added.extend(entities)
        )
    )
    spec_driven = {
        entity.unique_id: entity
        for entity in added
        if isinstance(entity, sensor_definitions.OptimizedEveusSensor)
    }
    for entity in spec_driven.values():
        disable_state_writes(entity)
    return spec_driven


def _metadata(entity) -> tuple:
    def _plain(value):
        return None if value is None else str(getattr(value, "value", value))

    return (
        _plain(entity.device_class),
        _plain(entity.state_class),
        _plain(entity.native_unit_of_measurement),
        entity.suggested_display_precision,
        _plain(entity.entity_category),
    )


@pytest.mark.parametrize(
    ("phases", "expected"),
    [
        (1, _ONE_PHASE_SENSORS),
        (3, {**_ONE_PHASE_SENSORS, **_THREE_PHASE_EXTRA}),
    ],
    ids=["1-phase", "3-phase"],
)
def test_sensor_identity_and_statistics_metadata_are_stable(phases, expected) -> None:
    """unique_id is what history, dashboards and automations hang on, and
    device/state class + unit decide what the recorder keeps as statistics.
    Any change here is a breaking change for every installed charger."""
    sensors = _set_up_sensors(phases=phases)

    assert {uid: _metadata(entity) for uid, entity in sensors.items()} == expected


@pytest.mark.parametrize(
    "unique_id",
    ["eveus_session_time", "eveus_active_rate_cost", "eveus_connection_quality"],
)
def test_sensors_that_explain_their_value_publish_attributes(real_main, unique_id) -> None:
    entity = _set_up_sensors(real_main)[unique_id]

    entity._handle_coordinator_update()

    assert entity.extra_state_attributes


def test_active_rate_cost_names_the_rate_in_force(real_main) -> None:
    sensors = _set_up_sensors({**real_main, "activeTarif": 1})
    entity = sensors["eveus_active_rate_cost"]

    entity._handle_coordinator_update()

    assert entity.extra_state_attributes == {"rate_name": "Rate 2"}


def test_active_rate_cost_has_no_rate_name_when_the_charger_omits_it(real_main) -> None:
    payload = {k: v for k, v in real_main.items() if k != "activeTarif"}
    entity = _set_up_sensors(payload)["eveus_active_rate_cost"]

    entity._handle_coordinator_update()

    assert entity.extra_state_attributes == {}


@pytest.mark.parametrize(
    ("unique_id", "field"),
    [("eveus_current_set", "currentSet"), ("eveus_adaptive_current_limit", "aiModecurrent")],
)
def test_amp_setpoints_publish_the_same_float_reading_as_other_measurements(
    real_main, unique_id, field
) -> None:
    entity = _set_up_sensors({**real_main, field: 16})[unique_id]

    entity._handle_coordinator_update()

    assert entity.native_value == 16.0
    assert isinstance(entity.native_value, float)


def test_each_distinct_unmapped_charger_state_is_logged(
    real_main, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.eveus.utils import RateLog

    monkeypatch.setattr(sensor_definitions, "_SENSOR_FUNCTION_LOG", RateLog())
    caplog.set_level(logging.WARNING, logger=sensor_definitions.__name__)
    updater_data = {**real_main, "state": 20}
    entity = _set_up_sensors(updater_data)["eveus_state"]

    entity._handle_coordinator_update()
    entity._updater.data = {**real_main, "state": 21}
    entity._handle_coordinator_update()

    logged = [r.getMessage() for r in caplog.records if "unrecognized device state" in r.getMessage()]
    assert [m.rsplit(" ", 1)[-1] for m in logged] == ["20", "21"]


@pytest.mark.parametrize("kind", ["current", "voltage"])
@pytest.mark.parametrize("phase", [2, 3])
def test_extra_phase_sensors_look_like_phase_one(kind: str, phase: int) -> None:
    sensors = _set_up_sensors(phases=3)

    assert sensors[f"eveus_{kind}_phase_{phase}"].icon == sensors[f"eveus_{kind}"].icon


# --- cost sensor: accumulation window across restarts -----------------------


def _cost_sensor(extra: dict | None, state: str = "unavailable", attributes=None):
    entity = _set_up_sensors({})["eveus_session_cost"]

    async def _last_extra_data():
        return None if extra is None else RestoredExtraData(extra)

    entity.async_get_last_extra_data = _last_extra_data
    asyncio.run(
        entity._async_restore_state(
            SimpleNamespace(state=state, attributes=attributes or {})
        )
    )
    return entity


def _read(entity, session_money: float) -> None:
    entity._updater.data = {"state": 2, "sessionMoney": session_money}
    entity._handle_coordinator_update()


def test_a_cost_sensor_that_never_read_can_still_be_saved() -> None:
    entity = _set_up_sensors({})["eveus_session_cost"]

    assert entity.extra_restore_state_data.as_dict() == {"last_reset": None, "value": None}


def test_restart_during_an_outage_still_detects_a_reset_made_meanwhile() -> None:
    """Saved while unavailable: the window and the last cost come from the
    entity's own extra data. A lower first reading after the restart is a
    charger-side reset and must open a new accumulation window."""
    window = datetime(2026, 9, 1, tzinfo=timezone.utc)
    entity = _cost_sensor({"last_reset": window.isoformat(), "value": 50.0})

    assert entity.last_reset == window
    _read(entity, 10.0)

    assert entity.last_reset > window


def test_restart_during_an_outage_keeps_the_window_while_cost_keeps_rising() -> None:
    window = datetime(2026, 9, 1, tzinfo=timezone.utc)
    entity = _cost_sensor({"last_reset": window.isoformat(), "value": 50.0})

    _read(entity, 60.0)

    assert entity.last_reset == window


@pytest.mark.parametrize("saved", [100_000_000, -100_000_000])
def test_saved_cost_at_the_accepted_bound_survives_another_restart(saved) -> None:
    entity = _cost_sensor({"last_reset": None, "value": saved})

    assert entity.extra_restore_state_data.as_dict()["value"] == saved


def test_last_reset_restores_from_the_state_attribute_when_no_extra_data_exists() -> None:
    """States saved before the entity kept its own extra data carry the
    window only as the `last_reset` attribute string."""
    window = datetime(2026, 9, 1, tzinfo=timezone.utc)
    entity = _cost_sensor(
        None, state="12.5", attributes={"last_reset": window.isoformat()}
    )

    assert entity.last_reset == window
