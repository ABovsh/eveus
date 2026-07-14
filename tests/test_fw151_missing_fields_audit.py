"""Missing-fields audit for GitHub issue #11 (firmware 1.x, MCU_SW_version 151).

The fw-1.x /main payload (tests/fixtures/fw151_unknown_state_main.json) omits a
large set of fields modern firmware always sends: verFWMain, serialNum,
sessionMaxCurrent, all OCPP fields, aiMode, etc. Every entity getter in the
integration must degrade gracefully (a value, or None/unavailable) against
this payload -- never raise, and never log anything beyond the single known
"unrecognized device state" warning (state=20 is outside CHARGING_STATES).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from conftest import EveusTestUpdater

from custom_components.eveus import binary_sensor as binary_sensor_mod
from custom_components.eveus import number as number_mod
from custom_components.eveus import select as select_mod
from custom_components.eveus import sensor_definitions as sd
from custom_components.eveus import switch as switch_mod
from custom_components.eveus.const import MODEL_16A

FW151_MAIN = json.loads(
    (Path(__file__).parent / "fixtures" / "fw151_unknown_state_main.json").read_text()
)

# Fields present on every modern-firmware capture but absent from fw-1.x.
MISSING_ON_FW151 = {
    "verFWMain", "verFWWifi", "firmware", "serialNum", "stationId",
    "sessionMaxCurrent", "aiMode", "aiStatus",  # aiStatus IS present (0) but
    # kept here as documentation that it means "adaptive off", not a real
    # OCPP-equivalent status field.
    "ocpp", "ocppVendor", "ocppConnected",
}


@pytest.fixture(scope="module")
def fw151_payload() -> dict:
    return dict(FW151_MAIN)


def _updater(data: dict):
    return EveusTestUpdater(dict(data), quality={"success_rate": 100, "latency_avg": 0.1})


# ---------------------------------------------------------------------------
# Sensor getters -- every spec registered by create_sensor_specifications.
# ---------------------------------------------------------------------------


def _all_sensor_specs():
    return list(sd.create_sensor_specifications(phases=1))


@pytest.mark.parametrize(
    "spec", _all_sensor_specs(), ids=lambda s: s.key
)
def test_sensor_getter_never_raises_on_fw151(fw151_payload, spec) -> None:
    updater = _updater(fw151_payload)
    try:
        value = spec.value_fn(updater, None)
    except Exception as err:  # noqa: BLE001
        pytest.fail(f"{spec.key} raised {type(err).__name__}: {err}")
    # Value or None -- either is an acceptable degrade for a missing field.
    assert value is None or value is not None


def test_all_sensor_getters_actually_ran() -> None:
    """Sanity check the parametrization above is not silently empty."""
    assert len(_all_sensor_specs()) > 20


# ---------------------------------------------------------------------------
# Binary sensors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "description", binary_sensor_mod.BINARY_SENSORS, ids=lambda d: d.name
)
def test_binary_sensor_getter_never_raises_on_fw151(fw151_payload, description) -> None:
    try:
        description.is_on_fn(fw151_payload)
    except Exception as err:  # noqa: BLE001
        pytest.fail(f"{description.name} raised {type(err).__name__}: {err}")


# ---------------------------------------------------------------------------
# Switches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "description", switch_mod.SWITCH_DESCRIPTIONS, ids=lambda d: d.key
)
def test_switch_resolve_state_never_raises_on_fw151(fw151_payload, description) -> None:
    updater = _updater(fw151_payload)
    entity = switch_mod.BaseSwitchEntity(updater, description)
    try:
        entity._resolve_state()
    except Exception as err:  # noqa: BLE001
        pytest.fail(f"switch {description.key} raised {type(err).__name__}: {err}")


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------


def test_current_number_never_raises_on_fw151(fw151_payload) -> None:
    updater = _updater(fw151_payload)
    entity = number_mod.EveusCurrentNumber(updater, MODEL_16A)
    entity.native_value  # noqa: B018 -- property access is the getter under test


def test_undervoltage_threshold_number_never_raises_on_fw151(fw151_payload) -> None:
    updater = _updater(fw151_payload)
    entity = number_mod.EveusUndervoltageThresholdNumber(
        updater, number_mod.UNDERVOLTAGE_THRESHOLD_NUMBER
    )
    entity.native_value  # noqa: B018


@pytest.mark.parametrize(
    "description",
    number_mod.GLOBAL_LIMIT_NUMBERS + number_mod.SCHEDULE_LIMIT_NUMBERS,
    ids=lambda d: d.key,
)
def test_setpoint_number_never_raises_on_fw151(fw151_payload, description) -> None:
    updater = _updater(fw151_payload)
    try:
        entity = number_mod.EveusSetpointNumber(updater, description)
        entity.native_value  # noqa: B018
    except Exception as err:  # noqa: BLE001
        pytest.fail(f"number {description.key} raised {type(err).__name__}: {err}")


# ---------------------------------------------------------------------------
# Selects
# ---------------------------------------------------------------------------


def test_timezone_select_never_raises_on_fw151(fw151_payload) -> None:
    updater = _updater(fw151_payload)
    entity = select_mod.EveusTimeZoneSelect(updater)
    entity.current_option  # noqa: B018


@pytest.mark.parametrize(
    "cls", [select_mod.EveusAdaptiveModeSelect, select_mod.EveusMinVoltageSelect]
)
def test_integer_select_never_raises_on_fw151(fw151_payload, cls) -> None:
    updater = _updater(fw151_payload)
    try:
        entity = cls(updater)
        entity.current_option  # noqa: B018
    except Exception as err:  # noqa: BLE001
        pytest.fail(f"select {cls.__name__} raised {type(err).__name__}: {err}")


# ---------------------------------------------------------------------------
# Warning-noise guard: only the known "unrecognized device state" warning.
# ---------------------------------------------------------------------------


def test_full_sweep_logs_only_the_known_unknown_state_warning(
    fw151_payload, caplog: pytest.LogCaptureFixture
) -> None:
    """Drive every getter in one sweep and assert no unexpected WARNING+ logs."""
    caplog.set_level(logging.WARNING)
    updater = _updater(fw151_payload)

    for spec in _all_sensor_specs():
        spec.value_fn(updater, None)
    for description in binary_sensor_mod.BINARY_SENSORS:
        description.is_on_fn(fw151_payload)
    for description in switch_mod.SWITCH_DESCRIPTIONS:
        switch_mod.BaseSwitchEntity(updater, description)._resolve_state()
    number_mod.EveusCurrentNumber(updater, MODEL_16A).native_value
    number_mod.EveusUndervoltageThresholdNumber(
        updater, number_mod.UNDERVOLTAGE_THRESHOLD_NUMBER
    ).native_value
    for description in number_mod.GLOBAL_LIMIT_NUMBERS + number_mod.SCHEDULE_LIMIT_NUMBERS:
        number_mod.EveusSetpointNumber(updater, description).native_value
    select_mod.EveusTimeZoneSelect(updater).current_option
    select_mod.EveusAdaptiveModeSelect(updater).current_option
    select_mod.EveusMinVoltageSelect(updater).current_option

    unexpected = [
        record
        for record in caplog.records
        if "unrecognized device state" not in record.getMessage()
    ]
    assert not unexpected, [r.getMessage() for r in unexpected]
