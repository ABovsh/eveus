"""Unit tests for entity construction."""
from __future__ import annotations

import logging

from custom_components.eveus.number import EveusCurrentNumber
from custom_components.eveus.sensor_definitions import OptimizedEveusSensor, SensorSpec, SensorType
from custom_components.eveus.switch import (
    EveusOneChargeSwitch,
    EveusResetCounterASwitch,
    EveusStopChargingSwitch,
)


class _Updater:
    host = "192.168.1.50"
    available = True
    last_update_success = True

    def __init__(self) -> None:
        self.available = True
        self.last_update_success = True
        self.data = {
            "currentSet": "16",
            "evseEnabled": "1",
            "oneCharge": "0",
            "IEM1": "5.5",
            "powerMeas": "7200",
        }

    def async_add_listener(self, *args: object, **kwargs: object):
        return lambda: None


def test_sensor_uses_fresh_coordinator_data_without_ttl_cache() -> None:
    updater = _Updater()
    spec = SensorSpec(
        key="power",
        name="Power",
        value_fn=lambda updater, hass: float(updater.data["powerMeas"]),
        sensor_type=SensorType.MEASUREMENT,
    )
    sensor = OptimizedEveusSensor(updater, spec)
    sensor.hass = object()
    sensor.async_write_ha_state = lambda: None

    updater.data["powerMeas"] = "7200"
    sensor._handle_coordinator_update()
    assert sensor.native_value == 7200

    updater.data["powerMeas"] = "1000"
    sensor._handle_coordinator_update()
    assert sensor.native_value == 1000


def test_base_entity_availability_grace_and_cache_paths() -> None:
    updater = _Updater()
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="power",
            name="Power",
            value_fn=lambda updater, hass: float(updater.data["powerMeas"]),
            sensor_type=SensorType.MEASUREMENT,
        ),
    )

    assert entity.available is True
    assert entity.get_cached_data_value("powerMeas") == "7200"
    updater.data = {}
    assert entity.get_cached_data_value("powerMeas") == "7200"
    assert entity.device_info["name"] == "Eveus EV Charger"

    updater.available = False
    entity._unavailable_since = 0
    entity._update_availability_state()
    assert entity.available is False


def test_available_property_is_pure_until_coordinator_update() -> None:
    updater = _Updater()
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="power",
            name="Power",
            value_fn=lambda updater, hass: float(updater.data["powerMeas"]),
            sensor_type=SensorType.MEASUREMENT,
        ),
    )
    updater.available = False

    assert entity.available is True
    assert entity._unavailable_since is None


def test_sensor_coordinator_update_writes_only_when_state_changes() -> None:
    updater = _Updater()
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="power",
            name="Power",
            value_fn=lambda updater, hass: float(updater.data["powerMeas"]),
            sensor_type=SensorType.MEASUREMENT,
        ),
    )
    writes = 0

    def write_state() -> None:
        nonlocal writes
        writes += 1

    entity.hass = object()
    entity.async_write_ha_state = write_state

    entity._handle_coordinator_update()
    entity._handle_coordinator_update()
    updater.data["powerMeas"] = "6000"
    entity._handle_coordinator_update()

    assert writes == 2
    assert entity.native_value == 6000


def test_sensor_coordinator_update_clears_value_after_grace_period() -> None:
    updater = _Updater()
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="power",
            name="Power",
            value_fn=lambda updater, hass: float(updater.data["powerMeas"]),
            sensor_type=SensorType.MEASUREMENT,
        ),
    )
    entity.hass = object()
    entity.async_write_ha_state = lambda: None
    entity._handle_coordinator_update()

    updater.available = False
    entity._unavailable_since = 0
    entity._handle_coordinator_update()

    assert entity.available is False
    assert entity.native_value is None


def test_sensor_value_errors_are_contained() -> None:
    updater = _Updater()
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="bad",
            name="Bad",
            value_fn=lambda updater, hass: (_ for _ in ()).throw(ValueError("boom")),
            sensor_type=SensorType.MEASUREMENT,
        ),
    )

    assert entity.native_value is None


def test_entity_unavailable_transition_is_quiet_at_normal_log_levels(
    caplog,
) -> None:
    updater = _Updater()
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="power",
            name="Power",
            value_fn=lambda updater, hass: float(updater.data["powerMeas"]),
            sensor_type=SensorType.MEASUREMENT,
        ),
    )
    updater.available = False
    entity._unavailable_since = 0

    with caplog.at_level(logging.INFO, logger="custom_components.eveus.common_base"):
        entity._update_availability_state()
        assert entity.available is False

    assert caplog.records == []


def test_base_entity_availability_restores_after_grace_period() -> None:
    updater = _Updater()
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="power",
            name="Power",
            value_fn=lambda updater, hass: float(updater.data["powerMeas"]),
            sensor_type=SensorType.MEASUREMENT,
        ),
    )
    entity._unavailable_since = 0
    entity._last_known_available = False

    entity._update_availability_state()
    assert entity.available is True
    assert entity._unavailable_since is None
    assert entity._last_known_available is True


def test_base_entity_device_info_falls_back_when_payload_is_malformed() -> None:
    updater = _Updater()
    updater.data = "not-a-dict"
    entity = OptimizedEveusSensor(
        updater,
        SensorSpec(
            key="power",
            name="Power",
            value_fn=lambda updater, hass: None,
            sensor_type=SensorType.MEASUREMENT,
        ),
    )

    assert entity.device_info == {
        "identifiers": {("eveus", "192.168.1.50")},
        "name": "Eveus EV Charger",
        "manufacturer": "Eveus",
        "model": "Eveus EV Charger",
        "sw_version": "Unknown",
        "hw_version": "Unknown",
        "configuration_url": "http://192.168.1.50",
    }


def test_control_availability_mixin_clears_optimistic_number_state_after_grace() -> None:
    updater = _Updater()
    entity = EveusCurrentNumber(updater, "16A")

    updater.available = False
    entity._unavailable_since = 0
    entity._optimistic_value = 12
    entity._last_known_available = True

    entity._update_availability_state()
    assert entity.available is False
    assert entity._optimistic_value is None


def test_control_unavailable_transition_is_quiet_at_normal_log_levels(caplog) -> None:
    updater = _Updater()
    entity = EveusCurrentNumber(updater, "16A")

    updater.available = False
    entity._unavailable_since = 0
    entity._optimistic_value = 12
    entity._last_known_available = True

    with caplog.at_level(logging.INFO, logger="custom_components.eveus.common_base"):
        entity._update_availability_state()
        assert entity.available is False

    assert caplog.records == []


def test_switch_entities_keep_backward_compatible_unique_ids() -> None:
    updater = _Updater()

    assert EveusStopChargingSwitch(updater).unique_id == "eveus_stop_charging"
    assert EveusOneChargeSwitch(updater).unique_id == "eveus_one_charge"
    assert EveusResetCounterASwitch(updater).unique_id == "eveus_reset_counter_a"


def test_number_entity_keeps_backward_compatible_unique_id_and_limits() -> None:
    entity = EveusCurrentNumber(_Updater(), "16A")

    assert entity.unique_id == "eveus_charging_current"
    assert entity.native_min_value == 7
    assert entity.native_max_value == 16


def test_control_state_properties_do_not_mutate_cached_device_state() -> None:
    updater = _Updater()
    number = EveusCurrentNumber(updater, "16A")
    switch = EveusOneChargeSwitch(updater)

    assert number.native_value == 16
    assert number._last_device_value is None
    assert switch.is_on is False
    assert switch._last_device_state is None
