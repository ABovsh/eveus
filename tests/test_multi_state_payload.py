"""Multi-state real-payload coverage (design Part 1c).

`test_real_payload_schema.py` runs every getter against ONE real snapshot
(charge-complete/idle). Real bugs, though, are state-dependent: residual standby
power fabricating an ETA in Connected/Complete, substate mapping differing in
Error state, getters that only fire mid-session. This module overlays the real
102-field capture with each charger lifecycle state (see
`conftest.STATE_VARIANTS`) and asserts the production getters stay well-behaved
in every state.

A truly-live charging snapshot (state=4 with real curMeas1/powerMeas) is
captured opportunistically on the next real session; until then `charging` is a
field-overlay on the real schema, which still exercises the state-gated paths.
"""
from __future__ import annotations

from types import SimpleNamespace

from custom_components.eveus import sensor_definitions as sd
from custom_components.eveus.const import CHARGING_STATES


def _updater(data: dict) -> SimpleNamespace:
    return SimpleNamespace(
        data=data,
        available=True,
        connection_quality={"success_rate": 100, "latency_avg": 0.1},
    )


# Pure payload getters (no HA helper-entity dependency). Each must survive every
# state and return None or a real number — never raise, never a wrong type.
_PURE_GETTERS = [
    sd.get_voltage,
    sd.get_current,
    sd.get_power,
    sd.get_session_energy,
    sd.get_total_energy,
    sd.get_counter_a_energy,
    sd.get_counter_b_energy,
    sd.get_box_temperature,
    sd.get_plug_temperature,
    sd.get_battery_voltage,
    sd.get_session_cost,
]


def test_every_pure_getter_survives_every_state(main_state_variant) -> None:
    upd = _updater(main_state_variant)
    for getter in _PURE_GETTERS:
        value = getter(upd, None)
        assert value is None or isinstance(value, (int, float)), (
            f"{getter.__name__} returned {type(value).__name__} "
            f"in state={main_state_variant['state']}"
        )


def test_charger_state_label_resolves_in_every_state(main_state_variant) -> None:
    upd = _updater(main_state_variant)
    label = sd.get_charger_state(upd, None)
    assert label == CHARGING_STATES[main_state_variant["state"]]


def test_substate_resolves_in_every_state(main_state_variant) -> None:
    """Error-state substate uses a different mapping than normal states."""
    assert sd.get_charger_substate(_updater(main_state_variant), None) is not None


def test_adaptive_state_resolves_in_every_state(main_state_variant) -> None:
    state = sd.get_adaptive_charging_state(_updater(main_state_variant), None)
    assert state in {"Off", "Voltage", "Auto", "Power"}
