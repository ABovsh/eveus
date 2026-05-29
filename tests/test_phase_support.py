"""Tests for 3-phase support and leakage sensors."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import voluptuous as vol
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from conftest import TEST_HOST, TEST_PASSWORD, TEST_USERNAME
from custom_components.eveus.config_flow import (
    build_user_data_schema,
    normalize_user_input,
)
from custom_components.eveus.const import (
    CONF_MODEL,
    CONF_PHASES,
    CONF_SCHEME,
    DEFAULT_PHASES,
    MODEL_16A,
    PHASE_OPTIONS,
)
from custom_components.eveus import sensor as sensor_module
from custom_components.eveus.sensor_definitions import get_sensor_specifications


class _Updater:
    host = TEST_HOST
    available = True
    last_update_success = True
    scheme = "http"
    username = TEST_USERNAME
    password = TEST_PASSWORD

    def __init__(self) -> None:
        self.data: dict[str, object] = {}

    def async_add_listener(self, *args: object, **kwargs: object):
        return lambda: None


def test_phase_options_are_one_and_three() -> None:
    assert PHASE_OPTIONS == [1, 3]
    assert DEFAULT_PHASES == 1


def test_user_data_schema_includes_phase_selector() -> None:
    schema = build_user_data_schema()
    assert CONF_PHASES in {str(k) for k in schema.schema}


def test_user_data_schema_defaults_to_one_phase() -> None:
    schema = build_user_data_schema()
    data = schema(
        {
            CONF_HOST: TEST_HOST,
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: TEST_PASSWORD,
            CONF_MODEL: MODEL_16A,
        }
    )
    assert data[CONF_PHASES] == 1


def test_user_data_schema_rejects_two_phases() -> None:
    schema = build_user_data_schema()
    with pytest.raises(vol.Invalid) as exc_info:
        schema(
            {
                CONF_HOST: TEST_HOST,
                CONF_USERNAME: TEST_USERNAME,
                CONF_PASSWORD: TEST_PASSWORD,
                CONF_MODEL: MODEL_16A,
                CONF_PHASES: 2,
            }
        )
    assert "value must be one of" in str(exc_info.value)


def test_user_data_schema_preserves_existing_three_phase_default() -> None:
    schema = build_user_data_schema({CONF_PHASES: 3})
    data = schema(
        {
            CONF_HOST: TEST_HOST,
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: TEST_PASSWORD,
            CONF_MODEL: MODEL_16A,
        }
    )
    assert data[CONF_PHASES] == 3


def test_normalize_user_input_coerces_string_phases() -> None:
    data = normalize_user_input(
        {
            CONF_HOST: TEST_HOST,
            CONF_USERNAME: TEST_USERNAME,
            CONF_PASSWORD: TEST_PASSWORD,
            CONF_MODEL: MODEL_16A,
            CONF_PHASES: "3",
        }
    )
    assert data[CONF_PHASES] == 3


def test_leakage_sensors_present_for_one_phase() -> None:
    names = {s.name for s in get_sensor_specifications(phases=1)}
    assert "Leakage Current" in names
    assert "Leakage Current Peak" in names


def test_three_phase_sensors_absent_for_one_phase() -> None:
    names = {s.name for s in get_sensor_specifications(phases=1)}
    for n in ("Current Phase 2", "Current Phase 3", "Voltage Phase 2", "Voltage Phase 3"):
        assert n not in names


def test_three_phase_sensors_present_for_three_phase() -> None:
    names = {s.name for s in get_sensor_specifications(phases=3)}
    for n in ("Current Phase 2", "Current Phase 3", "Voltage Phase 2", "Voltage Phase 3"):
        assert n in names


def test_sensor_setup_includes_three_phase_sensors_when_runtime_phases_is_3() -> None:
    added: list[object] = []
    entry = SimpleNamespace(
        title="Eveus (test)",
        data={CONF_MODEL: MODEL_16A},
        runtime_data=SimpleNamespace(
            updater=_Updater(),
            device_number=4,
            soc_calculator=object(),
            phases=3,
        ),
    )

    asyncio.run(
        sensor_module.async_setup_entry(
            object(),
            entry,
            lambda entities, update_before_add=False: added.extend(entities),
        )
    )

    names = {getattr(e, "name", None) for e in added}
    assert "Current Phase 2" in names
    assert "Voltage Phase 3" in names


def test_sensor_setup_excludes_three_phase_sensors_when_runtime_phases_is_1() -> None:
    added: list[object] = []
    entry = SimpleNamespace(
        title="Eveus (test)",
        data={CONF_MODEL: MODEL_16A},
        runtime_data=SimpleNamespace(
            updater=_Updater(),
            device_number=5,
            soc_calculator=object(),
            phases=1,
        ),
    )

    asyncio.run(
        sensor_module.async_setup_entry(
            object(),
            entry,
            lambda entities, update_before_add=False: added.extend(entities),
        )
    )

    names = {getattr(e, "name", None) for e in added}
    assert "Current Phase 2" not in names
    assert "Voltage Phase 2" not in names
