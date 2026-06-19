"""Named regression anchors for every reported & resolved GitHub issue.

One test per closed bug, linked to its issue, so the guarantee can't silently
lapse as the code evolves. These intentionally reuse production code paths (not
private re-implementations). Behavior detail lives in the per-area test modules;
this file is the greppable index that says "issue N stays fixed".

Open feature requests are tracked on GitHub, not here:
  - #4  Eveus Pro 16A 3Ф / 32A 1Ф hybrid model — feature request, no regression.
  - #1  Simple Mode — feature request (shipped as soc_mode=basic).
"""
from __future__ import annotations

import pytest
import voluptuous_serialize
from conftest import (
    TEST_HOST,
    TEST_PASSWORD,
    TEST_USERNAME,
    EveusTestUpdater,
    disable_state_writes,
)

from custom_components.eveus import config_flow as cf
from custom_components.eveus.const import CONF_PHASES, PHASE_OPTIONS


def _diag_sensor(updater):
    from custom_components.eveus.sensor_definitions import (
        OptimizedEveusSensor,
        SensorSpec,
        SensorType,
    )

    spec = SensorSpec(
        key="test_diag",
        name="Test Diag",
        value_fn=lambda _updater, _hass: 1,
        sensor_type=SensorType.DIAGNOSTIC,
    )
    sensor = OptimizedEveusSensor(updater, spec)
    disable_state_writes(sensor)
    return sensor


# ---------------------------------------------------------------------------
# Issue #5 — "value must be one of [1, 3]" selecting phases/current at setup.
# https://github.com/ABovsh/eveus/issues/5
# The mobile frontend submits select values as strings; the schema must coerce
# "1"/"3" to ints instead of rejecting them.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phase_str", ["1", "3"])
def test_issue_5_phase_count_submitted_as_string_is_accepted(phase_str) -> None:
    result = cf.build_user_data_schema()(
        {
            "host": TEST_HOST,
            "username": TEST_USERNAME,
            "password": TEST_PASSWORD,
            "model": "16A",
            CONF_PHASES: phase_str,
            "soc_mode": "basic",
        }
    )
    assert result[CONF_PHASES] == int(phase_str)
    assert result[CONF_PHASES] in PHASE_OPTIONS


# ---------------------------------------------------------------------------
# Issue #6 — sensor.eveus_ev_charger_system_time polluted the HA recorder DB.
# https://github.com/ABovsh/eveus/issues/6
# The entity was removed; no production sensor spec may resurrect it.
# ---------------------------------------------------------------------------


def test_issue_6_system_time_sensor_is_not_registered() -> None:
    from custom_components.eveus.sensor_definitions import create_sensor_specifications

    keys = {spec.key for spec in create_sensor_specifications(phases=1)}
    assert "system_time" not in keys
    assert not any("system_time" in k for k in keys)


# ---------------------------------------------------------------------------
# Issue #7 — a charger firmware update was not recognized until the integration
# was reloaded. https://github.com/ABovsh/eveus/issues/7
# Device metadata must refresh in place when the reported firmware changes.
# ---------------------------------------------------------------------------


def test_issue_7_firmware_update_recognized_without_reload() -> None:
    updater = EveusTestUpdater({"verFWMain": "1.0"})
    sensor = _diag_sensor(updater)
    sensor._maybe_finalize_device_info()
    assert sensor._attr_device_info["sw_version"] == "1.0"

    # New firmware string arrives on a normal poll — no reload.
    updater.data = {"verFWMain": "2.0"}
    sensor._maybe_finalize_device_info()
    assert sensor._attr_device_info["sw_version"] == "2.0"


# ---------------------------------------------------------------------------
# Issue #8 — "Config flow could not be loaded: 500 Internal Server Error".
# https://github.com/ABovsh/eveus/issues/8
# Every flow-step schema must serialize the way HA serializes it for the
# frontend; a non-serializable schema 500s before the form renders.
# ---------------------------------------------------------------------------


def test_issue_8_user_step_schema_is_frontend_serializable() -> None:
    import homeassistant.helpers.config_validation as cv

    voluptuous_serialize.convert(
        cf.build_user_data_schema({}), custom_serializer=cv.custom_serializer
    )
