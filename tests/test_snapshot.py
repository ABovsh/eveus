"""Contract for the typed /main snapshot (P2.1).

One parse of the charger payload, one place that knows each field's converter
and its physical bounds. Every consumer reads the parsed value and never
re-checks the bounds, so two consumers of the same field cannot drift apart.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.eveus.const import (
    BATTERY_VBAT_MAX_PLAUSIBLE_VOLTS,
    DEVICE_STATE_CHARGING,
    DEVICE_STATE_ERROR,
    DEVICE_STATE_STANDBY,
    LEGACY_RAW_STATE_KEY,
    MAX_ENERGY_KWH,
    MAX_POWER_W,
    MAX_SESSION_TIME_SECONDS,
)
from custom_components.eveus.snapshot import EveusSnapshot

_REAL_PAYLOAD = json.loads(
    (Path(__file__).parent / "fixtures" / "real_main_response.json").read_text(
        encoding="utf-8"
    )
)


def _parse(**overrides):
    payload = {**_REAL_PAYLOAD, **overrides}
    return EveusSnapshot.parse(payload, None)


# --- the parse itself -------------------------------------------------------


def test_real_payload_parses_into_typed_fields() -> None:
    """The shipped live capture produces the values the sensors publish."""
    snap = EveusSnapshot.parse(_REAL_PAYLOAD, None)
    assert snap.state == int(_REAL_PAYLOAD["state"])
    assert snap.current_set == float(_REAL_PAYLOAD["currentSet"])
    assert snap.power_w == float(_REAL_PAYLOAD["powerMeas"])
    assert snap.cur_meas[0] == float(_REAL_PAYLOAD["curMeas1"])
    assert snap.volt_meas[0] == float(_REAL_PAYLOAD["voltMeas1"])
    assert snap.session_energy_kwh == float(_REAL_PAYLOAD["sessionEnergy"])
    assert snap.session_time_s == int(_REAL_PAYLOAD["sessionTime"])
    assert snap.session_money == float(_REAL_PAYLOAD["sessionMoney"])


def test_raw_payload_is_kept_verbatim() -> None:
    """Diagnostics and the dashboard card still need the untouched dict."""
    payload = {**_REAL_PAYLOAD, LEGACY_RAW_STATE_KEY: 20}
    snap = EveusSnapshot.parse(payload, None)
    assert snap.raw == payload
    assert snap.raw[LEGACY_RAW_STATE_KEY] == 20


def test_absent_field_reads_as_none() -> None:
    payload = {k: v for k, v in _REAL_PAYLOAD.items() if k != "powerMeas"}
    assert EveusSnapshot.parse(payload, None).power_w is None


@pytest.mark.parametrize(
    "key, value",
    [
        ("powerMeas", MAX_POWER_W + 1),
        ("powerMeas", -1),
        ("sessionEnergy", MAX_ENERGY_KWH + 1),
        ("sessionTime", MAX_SESSION_TIME_SECONDS + 1),
        ("sessionTime", -1),
        ("temperature1", 1e9),
        ("vBat", BATTERY_VBAT_MAX_PLAUSIBLE_VOLTS + 0.1),
        ("vBat", 0),
        ("RSSI", 1),
        ("RSSI", -121),
        ("leakValue", -1),
    ],
)
def test_out_of_physical_bounds_reads_as_none(key, value) -> None:
    """A finite-but-impossible reading is unusable, not a small number."""
    assert _parse(**{key: value}).get(key) is None


@pytest.mark.parametrize("bad", [True, False, "unknown", "", None, float("nan")])
def test_unusable_scalars_read_as_none(bad) -> None:
    assert _parse(powerMeas=bad).power_w is None


def test_int_field_rejects_a_fractional_float() -> None:
    """`state` 4.9 must not truncate to a plausible enum value."""
    assert _parse(state=4.9).state is None


def test_int_field_accepts_an_integral_float() -> None:
    assert _parse(state=4.0).state == 4


# --- model-bounded fields ---------------------------------------------------


def test_current_set_is_bounded_by_the_configured_model() -> None:
    assert EveusSnapshot.parse({**_REAL_PAYLOAD, "currentSet": 32}, "16A").current_set is None
    assert EveusSnapshot.parse({**_REAL_PAYLOAD, "currentSet": 16}, "16A").current_set == 16


def test_model_bound_falls_back_to_the_largest_model() -> None:
    """Without a configured model a corrupt setpoint is still rejected."""
    assert _parse(currentSet=999).current_set is None
    assert _parse(currentSet=48).current_set == 48


def test_adaptive_current_limit_uses_the_same_model_bound() -> None:
    assert EveusSnapshot.parse(
        {**_REAL_PAYLOAD, "aiModecurrent": 32}, "16A"
    ).get("aiModecurrent") is None


# --- derived views ----------------------------------------------------------


@pytest.mark.parametrize(
    "state, expected",
    [(DEVICE_STATE_CHARGING, True), (6, True), (3, False), (5, False),
     (DEVICE_STATE_STANDBY, False), (DEVICE_STATE_ERROR, None), (20, None)],
)
def test_session_active_view(state, expected) -> None:
    assert _parse(state=state).session_active is expected


@pytest.mark.parametrize(
    "state, expected",
    [(3, True), (DEVICE_STATE_CHARGING, True), (5, True), (6, True),
     (DEVICE_STATE_STANDBY, False), (DEVICE_STATE_ERROR, None), (20, None)],
)
def test_plugged_in_view(state, expected) -> None:
    """Indeterminate in Error, and unknowable for an unmapped firmware code."""
    assert _parse(state=state).plugged_in is expected


def test_modern_firmware_view_follows_the_payload_marker() -> None:
    assert _parse().modern_firmware is True
    legacy = {k: v for k, v in _REAL_PAYLOAD.items() if k not in ("verFWMain", "firmware")}
    assert EveusSnapshot.parse(legacy, None).modern_firmware is False


# --- accessors --------------------------------------------------------------


def test_get_rejects_a_key_the_field_table_does_not_know() -> None:
    """A typo must be loud here rather than silently read as `unknown`."""
    with pytest.raises(KeyError):
        _parse().get("powerMeasurement")


def test_get_int_enforces_integrality_on_a_float_field() -> None:
    """Schedule setpoints are whole numbers; 7.5 is corrupt, not 7."""
    assert _parse(sh1CurrentValue=7.5).get_int("sh1CurrentValue") is None
    assert _parse(sh1CurrentValue=7).get_int("sh1CurrentValue") == 7


def test_empty_snapshot_answers_none_for_every_field() -> None:
    empty = EveusSnapshot.empty()
    assert empty.raw == {}
    assert empty.state is None
    assert empty.power_w is None
    assert empty.session_active is None
    assert empty.modern_firmware is False
