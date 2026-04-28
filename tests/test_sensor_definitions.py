"""Unit tests for generated sensor value definitions."""
from __future__ import annotations

from types import SimpleNamespace

from homeassistant.helpers.entity import EntityCategory

from custom_components.eveus import sensor_definitions as sensors


def _updater(data: dict[str, object], *, available: bool = True) -> SimpleNamespace:
    return SimpleNamespace(data=data, available=available, connection_quality={})


def test_measurement_getters_convert_device_payload_values() -> None:
    updater = _updater(
        {
            "voltMeas1": "229.6",
            "curMeas1": "14.24",
            "powerMeas": "3265.55",
            "currentSet": "16",
        }
    )

    assert sensors.get_voltage(updater, None) == 230
    assert sensors.get_current(updater, None) == 14.2
    assert sensors.get_power(updater, None) == 3265.6
    assert sensors.get_current_set(updater, None) == 16


def test_state_getters_map_known_values() -> None:
    updater = _updater({"state": "4", "subState": "1", "ground": "1"})

    assert sensors.get_charger_state(updater, None) == "Charging"
    assert sensors.get_charger_substate(updater, None) == "Limited by User"
    assert sensors.get_ground_status(updater, None) == "Connected"


def test_error_state_uses_error_mapping() -> None:
    updater = _updater({"state": "7", "subState": "10"})

    assert sensors.get_charger_substate(updater, None) == "Overcurrent"


def test_rate_costs_are_converted_from_cents() -> None:
    updater = _updater(
        {
            "activeTarif": "1",
            "tarif": "264",
            "tarifAValue": "132",
            "tarifBValue": "400",
            "tarifAEnable": "1",
            "tarifBEnable": "0",
        }
    )

    assert sensors.get_primary_rate_cost(updater, None) == 2.64
    assert sensors.get_rate2_cost(updater, None) == 1.32
    assert sensors.get_rate3_cost(updater, None) == 4.0
    assert sensors.get_active_rate_cost(updater, None) == 1.32
    assert sensors._make_rate_status_getter("tarifAEnable")(updater, None) == "Enabled"
    assert sensors._make_rate_status_getter("tarifBEnable")(updater, None) == "Disabled"


def test_getters_return_none_when_updater_is_unavailable() -> None:
    updater = _updater({"powerMeas": "1200"}, available=False)

    assert sensors.get_power(updater, None) is None
    assert sensors.get_charger_state(updater, None) is None


def test_sensor_specification_factory_exposes_expected_entities() -> None:
    specs = sensors.get_sensor_specifications()
    names = {spec.name for spec in specs}

    assert "Voltage" in names
    assert "Session Energy" in names
    assert "State" in names
    assert "Connection Quality" in names
    assert len(specs) >= 20


def test_status_like_entities_are_diagnostic() -> None:
    specs = {spec.name: spec for spec in sensors.get_sensor_specifications()}

    assert specs["Current Set"].category == EntityCategory.DIAGNOSTIC
    assert specs["Rate 2 Status"].category == EntityCategory.DIAGNOSTIC
    assert specs["Rate 3 Status"].category == EntityCategory.DIAGNOSTIC
