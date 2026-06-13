"""Hardening tests for 4.9.2-rc6: numeric safety, restore resiliency, HA contracts."""
from __future__ import annotations

import asyncio
import time

import pytest
import voluptuous as vol
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from conftest import EveusTestUpdater
from custom_components.eveus import ev_sensors
from custom_components.eveus import number as number_mod
from custom_components.eveus import sensor_definitions as sd
from custom_components.eveus import switch as switch_mod
from custom_components.eveus import utils


# ---------------------------------------------------------------------------
# F02 — explicit 0% efficiency-loss correction is preserved (not coerced to default)
# ---------------------------------------------------------------------------

def test_zero_soc_correction_is_preserved() -> None:
    calc = ev_sensors.CachedSOCCalculator()
    calc.set_value("soc_correction", 0.0)
    assert calc._effective_correction() == 0.0
    assert calc.soc_correction == 0.0


def test_missing_soc_correction_falls_back_to_default() -> None:
    calc = ev_sensors.CachedSOCCalculator()
    calc.set_value("soc_correction", None)
    assert calc._effective_correction() == ev_sensors.DEFAULT_SOC_CORRECTION


# ---------------------------------------------------------------------------
# F03 — present-but-invalid sessionEnergy is distinguishable from absent
# ---------------------------------------------------------------------------

def test_session_energy_invalid_when_present_and_negative() -> None:
    sensor = ev_sensors.EVSocKwhSensor(EveusTestUpdater({"sessionEnergy": -1.0}))
    assert sensor._session_energy_is_invalid() is True


def test_session_energy_not_invalid_when_absent() -> None:
    sensor = ev_sensors.EVSocKwhSensor(EveusTestUpdater({}))
    assert sensor._session_energy_is_invalid() is False


# ---------------------------------------------------------------------------
# F05 / F06 — coordinator state validation rejects bool and non-finite
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [True, False])
def test_get_safe_value_rejects_bool(bad: bool) -> None:
    assert utils.get_safe_value({"state": bad}, "state", int) is None


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_get_safe_value_rejects_non_finite(bad: float) -> None:
    assert utils.get_safe_value({"x": bad}, "x", int) is None
    assert utils.get_safe_value({"x": bad}, "x", float) is None


def test_state_bool_and_finite_guard_present_in_source() -> None:
    import inspect
    src = inspect.getsource(__import__("custom_components.eveus.common_network",
                                       fromlist=["x"]))
    assert "'state' field is boolean" in src
    assert "'state' field is not finite" in src


# ---------------------------------------------------------------------------
# F11 — switch reports unknown (None) instead of definite off on missing key
# ---------------------------------------------------------------------------

def test_switch_missing_key_resolves_unknown() -> None:
    description = switch_mod.SWITCH_DESCRIPTIONS[1]  # One Charge / oneCharge
    sw = switch_mod.BaseSwitchEntity(EveusTestUpdater({}), description, 1)
    assert sw._resolve_state() is None
    assert sw.is_on is None


def test_switch_valid_key_resolves_bool() -> None:
    description = switch_mod.SWITCH_DESCRIPTIONS[1]
    sw = switch_mod.BaseSwitchEntity(EveusTestUpdater({"oneCharge": 1}), description, 1)
    assert sw._resolve_state() is True


# ---------------------------------------------------------------------------
# F08 / F09 / F10 — restore seeds _last_successful_read so values survive grace window
# ---------------------------------------------------------------------------

def test_switch_restore_seeds_successful_read() -> None:
    from homeassistant.core import State
    description = switch_mod.SWITCH_DESCRIPTIONS[1]
    sw = switch_mod.BaseSwitchEntity(EveusTestUpdater({}), description, 1)
    before = time.time()
    asyncio.run(sw._async_restore_state(State("switch.x", "on")))
    assert sw._last_successful_read >= before
    assert sw._last_device_value is True


def test_number_restore_seeds_successful_read() -> None:
    from homeassistant.core import State
    num = number_mod.EveusCurrentNumber(EveusTestUpdater({}), "16A", 1)
    before = time.time()
    asyncio.run(num._async_restore_state(State("number.x", "12")))
    assert num._last_successful_read >= before
    assert num._last_device_value == 12.0


# ---------------------------------------------------------------------------
# F13 — host shape is validated through _split_host_and_scheme
# ---------------------------------------------------------------------------

def test_split_host_rejects_path_and_credentials() -> None:
    from custom_components.eveus.config_flow import _split_host_and_scheme
    with pytest.raises(vol.Invalid):
        _split_host_and_scheme("http://user:pass@1.2.3.4")
    with pytest.raises(vol.Invalid):
        _split_host_and_scheme("http://1.2.3.4/main")


# ---------------------------------------------------------------------------
# F18 — schedule current-limit attribute is dropped when above model max
# ---------------------------------------------------------------------------

def test_schedule_current_limit_dropped_when_above_model_max() -> None:
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


# ---------------------------------------------------------------------------
# F19 — corrupt charger clock is reported as unknown
# ---------------------------------------------------------------------------

def test_time_drift_rejects_negative_clock() -> None:
    assert sd.get_time_drift(
        EveusTestUpdater({"systemTime": -1, "timeZone": 3}), None
    ) is None


def test_time_drift_rejects_far_future_clock() -> None:
    assert sd.get_time_drift(
        EveusTestUpdater({"systemTime": 99999999999, "timeZone": 3}), None
    ) is None


# ---------------------------------------------------------------------------
# F04 — duplicate device numbers are detected
# ---------------------------------------------------------------------------

def test_is_device_number_taken_helper_exists() -> None:
    assert callable(utils.is_device_number_taken)


# ---------------------------------------------------------------------------
# F22 — reauth fires the plaintext warning
# ---------------------------------------------------------------------------

def test_reauth_emits_plaintext_warning_in_source() -> None:
    import inspect
    from custom_components.eveus import config_flow
    # The cleartext warning is centralized in validate_input so it fires before
    # the connect for every flow (setup/reconfigure/reauth/repair), including
    # failed attempts. Reauth reaches it by calling validate_input.
    assert "_warn_if_plaintext" in inspect.getsource(config_flow.validate_input)
    assert "validate_input" in inspect.getsource(
        config_flow.ConfigFlow.async_step_reauth_confirm
    )


# ---------------------------------------------------------------------------
# F16 — absolute cost sensors are MONETARY with ISO currency unit
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["counter_a_cost", "counter_b_cost", "session_cost"])
def test_cost_sensors_are_monetary_iso(key: str) -> None:
    by_key = {s.key: s for s in sd.get_sensor_specifications(1)}
    spec = by_key[key]
    assert spec.device_class == SensorDeviceClass.MONETARY
    assert spec.unit == "UAH"
    assert spec.state_class == SensorStateClass.TOTAL
