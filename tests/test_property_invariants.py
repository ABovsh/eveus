"""Property-based invariants for the hostile-firmware value layer (design Part 1a).

The integration's dominant bug class is "firmware values are hostile": booleans
where numbers are expected, non-finite floats, fractional setpoints, overflowing
divisions, out-of-range telemetry. Hand-picked example dicts can only cover the
cases someone thought of. These Hypothesis properties assert *invariants* that
must hold across the whole input space, so they catch the cases nobody listed.

Scope is deliberately the pure functions (`utils`, `_payload`) — no async, no HA
runtime — which keeps the properties fast, deterministic, and zero-maintenance.
"""
from __future__ import annotations

import math

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from custom_components.eveus._payload import PayloadError, validate_main_payload
from conftest import EveusTestUpdater, disable_state_writes
from custom_components.eveus.const import (
    CHARGING_STATES,
    MODEL_16A,
    MODEL_MAX_CURRENT,
    MODELS,
    PHASE_OPTIONS,
)
from custom_components.eveus.binary_sensor import (
    EveusCarConnectedBinarySensor,
    EveusOcppConnectedBinarySensor,
    EveusSessionActiveBinarySensor,
)
from custom_components.eveus.sensor_definitions import create_sensor_specifications
from custom_components.eveus.utils import (
    calculate_remaining_seconds,
    calculate_soc_kwh,
    calculate_soc_percent,
    get_safe_value,
)

# Bounded, deterministic profile: fast in CI, no flaky deadlines on the slow
# import path. This is a permanent low-maintenance gate, not an exploratory run.
settings.register_profile(
    "eveus",
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("eveus")

_MAX_16A = MODEL_MAX_CURRENT[MODEL_16A]

# Any scalar a charger, proxy, or corrupt poll could put in a /main field.
HOSTILE = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(10**9), max_value=10**9),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=12),
    st.lists(st.integers(), max_size=3),
    st.dictionaries(st.text(max_size=4), st.integers(), max_size=3),
)

# Hostile numeric-ish input for the SOC/ETA math (which calls float() internally).
HOSTILE_NUM = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(10**6), max_value=10**6),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=8),
)


# --- get_safe_value: never raises; output is None or a clean converted value ---

@given(value=HOSTILE)
def test_get_safe_value_float_returns_none_or_finite_float(value) -> None:
    out = get_safe_value({"k": value}, "k", float)
    assert out is None or (isinstance(out, float) and math.isfinite(out))


@given(value=HOSTILE)
def test_get_safe_value_int_returns_none_or_int(value) -> None:
    out = get_safe_value({"k": value}, "k", int)
    # bool is an int subclass but is rejected to default(None); guard against it.
    assert out is None or (isinstance(out, int) and not isinstance(out, bool))


@given(value=HOSTILE)
def test_get_safe_value_never_returns_bool_for_numeric_converter(value) -> None:
    """A boolean firmware value must never masquerade as 1/0."""
    assert not isinstance(get_safe_value({"k": value}, "k", float), bool)


@pytest.mark.parametrize("nonfinite", [float("nan"), float("inf"), float("-inf")])
def test_get_safe_value_str_converter_rejects_non_finite_float(nonfinite) -> None:
    """A non-finite float read as a string must fall back to the default.

    The pre-conversion non-finite guard is what stops ``str(nan)`` -> ``"nan"``
    leaking out for a firmware field read with the ``str`` converter (e.g. a
    version string). Found via a mutation spot-check: removing that guard
    otherwise goes uncaught because the post-conversion guard only covers floats.
    """
    assert get_safe_value({"k": nonfinite}, "k", str) is None
    # A genuine string is still returned unchanged.
    assert get_safe_value({"k": "GRM070A"}, "k", str) == "GRM070A"


# --- validate_main_payload: the device-availability contract ---

@given(
    state=st.sampled_from(sorted(CHARGING_STATES)),
    current_set=st.integers(min_value=0, max_value=_MAX_16A),
)
def test_valid_setpoint_is_never_rejected(state, current_set) -> None:
    """Any whole 0..model-max setpoint in a known state must pass.

    This is the 4.13.0 regression as a property: a sub-7 A `currentSet` (0..6) is
    a legitimate firmware state and must NOT make the device unavailable.
    """
    payload = {"state": state, "currentSet": current_set}
    assert validate_main_payload(payload, MODEL_16A) is payload


@given(neg=st.integers(min_value=-(10**6), max_value=-1))
def test_negative_setpoint_is_always_rejected(neg) -> None:
    with pytest.raises(PayloadError):
        validate_main_payload({"state": 2, "currentSet": neg}, MODEL_16A)


@given(over=st.integers(min_value=_MAX_16A + 1, max_value=10**6))
def test_over_model_max_setpoint_is_always_rejected(over) -> None:
    with pytest.raises(PayloadError):
        validate_main_payload({"state": 2, "currentSet": over}, MODEL_16A)


@given(
    model=st.sampled_from(MODELS),
    current_set=st.integers(min_value=0, max_value=max(MODEL_MAX_CURRENT.values()) + 5),
    state=st.sampled_from(sorted(CHARGING_STATES)),
)
def test_setpoint_payload_bounds_follow_selected_model(model, current_set, state) -> None:
    payload = {"state": state, "currentSet": current_set}
    if current_set <= MODEL_MAX_CURRENT[model]:
        assert validate_main_payload(payload, model) is payload
    else:
        with pytest.raises(PayloadError):
            validate_main_payload(payload, model)


@given(
    frac=st.floats(min_value=0.01, max_value=_MAX_16A - 0.01).filter(
        lambda x: not float(x).is_integer()
    )
)
def test_fractional_setpoint_is_always_rejected(frac) -> None:
    with pytest.raises(PayloadError):
        validate_main_payload({"state": 2, "currentSet": frac}, MODEL_16A)


@given(state=st.integers(min_value=8, max_value=10**6))
def test_out_of_domain_state_is_always_rejected(state) -> None:
    # 0..7 is the known domain; anything above is unknown telemetry.
    with pytest.raises(PayloadError):
        validate_main_payload({"state": state, "currentSet": 10}, MODEL_16A)


# --- SOC / ETA math: bounded, finite, never raises ---

@given(
    initial=HOSTILE_NUM,
    capacity=HOSTILE_NUM,
    energy=HOSTILE_NUM,
    loss=HOSTILE_NUM,
)
def test_soc_percent_is_always_a_bounded_percentage(initial, capacity, energy, loss) -> None:
    out = calculate_soc_percent(initial, capacity, energy, loss)
    assert isinstance(out, (int, float))
    assert 0 <= out <= 100


@given(
    initial=HOSTILE_NUM,
    capacity=HOSTILE_NUM,
    energy=HOSTILE_NUM,
    loss=HOSTILE_NUM,
)
def test_soc_kwh_is_always_finite_and_non_negative(initial, capacity, energy, loss) -> None:
    out = calculate_soc_kwh(initial, capacity, energy, loss)
    assert isinstance(out, (int, float))
    assert math.isfinite(out)
    assert out >= 0


@given(
    current=HOSTILE_NUM,
    target=HOSTILE_NUM,
    power=HOSTILE_NUM,
    capacity=HOSTILE_NUM,
    correction=HOSTILE_NUM,
)
def test_remaining_seconds_is_none_or_finite_non_negative(
    current, target, power, capacity, correction
) -> None:
    out = calculate_remaining_seconds(current, target, power, capacity, correction)
    assert out is None or (
        isinstance(out, float) and math.isfinite(out) and out >= 0
    )


# --- Entity factories: generated entities preserve model/phase contracts ---

@given(
    phases=st.sampled_from(PHASE_OPTIONS),
    model=st.sampled_from(MODELS),
    current_set=st.integers(min_value=0, max_value=max(MODEL_MAX_CURRENT.values()) + 5),
)
def test_current_set_sensor_factory_uses_model_bound(phases, model, current_set) -> None:
    specs = create_sensor_specifications(
        phases=phases,
        max_current=MODEL_MAX_CURRENT[model],
    )
    spec = next(item for item in specs if item.key == "current_set")
    sensor = spec.create_sensor(EveusTestUpdater({"currentSet": current_set}), 1)
    disable_state_writes(sensor)

    expected = current_set if current_set <= MODEL_MAX_CURRENT[model] else None
    assert sensor._get_sensor_value() == expected


@given(
    phases=st.sampled_from(PHASE_OPTIONS),
    model=st.sampled_from(MODELS),
)
def test_sensor_factory_unique_ids_are_stable_and_unique(phases, model) -> None:
    specs = create_sensor_specifications(
        phases=phases,
        max_current=MODEL_MAX_CURRENT[model],
    )
    sensors = [
        spec.create_sensor(EveusTestUpdater({}), device_number=2)
        for spec in specs
    ]
    unique_ids = [sensor.unique_id for sensor in sensors]

    assert len(unique_ids) == len(set(unique_ids))
    assert all(unique_id.startswith("eveus2_") for unique_id in unique_ids)


@given(
    state=st.integers(min_value=-2, max_value=10),
    ocpp_connected=HOSTILE,
)
def test_binary_sensor_factories_never_coerce_unknown_state_to_false(
    state, ocpp_connected
) -> None:
    updater = EveusTestUpdater({"state": state, "ocppconnected": ocpp_connected})
    entities = (
        EveusCarConnectedBinarySensor(updater),
        EveusSessionActiveBinarySensor(updater),
        EveusOcppConnectedBinarySensor(updater),
    )

    for entity in entities:
        disable_state_writes(entity)
        assert entity.is_on in (True, False, None)
    if state not in CHARGING_STATES or state == 7:
        assert entities[0].is_on is None
        assert entities[1].is_on is None
