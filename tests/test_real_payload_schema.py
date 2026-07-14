"""Schema-drift test: every value getter must work against a real /main payload.

The fixture in `tests/fixtures/real_main_response.json` is a verbatim capture
from a live Eveus Pro 1P 2024 (FW GRM070A-R3.05.2). If the charger firmware
ever renames or drops a field this integration reads, this test fails loudly —
which is what synthetic unit tests can't catch.

Refresh the fixture with:
    curl -s -u USER:PASS -X POST http://CHARGER/main \\
        | python3 -m json.tool > tests/fixtures/real_main_response.json
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import spec_value_fn

from custom_components.eveus._payload import PayloadError, validate_main_payload
from custom_components.eveus import sensor_definitions as sd
from custom_components.eveus.const import MODEL_16A


FIXTURE = Path(__file__).parent / "fixtures" / "real_main_response.json"


@pytest.fixture(scope="module")
def real_payload() -> dict:
    return json.loads(FIXTURE.read_text())


def _updater(data: dict) -> SimpleNamespace:
    return SimpleNamespace(data=data, available=True, connection_quality={"success_rate": 100, "latency_avg": 0.1})


# Every value getter and the field it reads from. If the field is dropped or
# renamed by firmware, the getter returns None and this test fails.
GETTERS = [
    (sd.get_voltage, "voltMeas1", float),
    (sd.get_current, "curMeas1", float),
    (sd.get_power, "powerMeas", float),
    (spec_value_fn("current_set"), "currentSet", int),
    (sd.get_session_energy, "sessionEnergy", float),
    (sd.get_total_energy, "totalEnergy", float),
    (sd.get_counter_a_energy, "IEM1", float),
    (sd.get_counter_b_energy, "IEM2", float),
    (sd.get_counter_a_cost, "IEM1_money", float),
    (sd.get_counter_b_cost, "IEM2_money", float),
    (sd.get_primary_rate_cost, "tarif", float),
    (sd.get_rate2_cost, "tarifAValue", float),
    (sd.get_rate3_cost, "tarifBValue", float),
    (sd.get_box_temperature, "temperature1", float),
    (sd.get_plug_temperature, "temperature2", float),
    (sd.get_battery_voltage, "vBat", float),
    (sd.get_session_cost, "sessionMoney", float),
    (spec_value_fn("adaptive_current_limit"), "aiModecurrent", int),
]


@pytest.mark.parametrize("getter,field,_type", GETTERS, ids=lambda x: getattr(x, "__name__", str(x)))
def test_getter_extracts_real_field(real_payload, getter, field, _type) -> None:
    assert field in real_payload, f"firmware drift: missing field `{field}`"
    value = getter(_updater(real_payload), None)
    assert value is not None, f"{getter.__name__} returned None for field `{field}`"
    assert isinstance(value, (int, float)), f"{getter.__name__} returned {type(value).__name__}"


def test_state_and_substate_resolve(real_payload) -> None:
    upd = _updater(real_payload)
    assert sd.get_charger_state(upd, None) is not None
    assert sd.get_charger_substate(upd, None) is not None
    assert sd.get_ground_status(upd, None) in {"Connected", "Not Connected"}


def test_session_time_and_time_drift(real_payload) -> None:
    upd = _updater(real_payload)
    assert sd.get_session_time(upd, None) is not None
    assert isinstance(sd.get_time_drift(upd, None), int)


def test_active_rate_resolves_to_known_slot(real_payload) -> None:
    upd = _updater(real_payload)
    # Whichever slot is active (0/1/2), the cost must resolve.
    assert sd.get_active_rate_cost(upd, None) == pytest.approx(4.32)


def test_adaptive_charging_state_resolves(real_payload) -> None:
    state = sd.get_adaptive_charging_state(_updater(real_payload), None)
    assert state in {"Off", "Voltage", "Auto", "Power"}


def test_schedule_slots_resolve(real_payload) -> None:
    upd = _updater(real_payload)
    for slot in (1, 2):
        assert sd._make_schedule_getter(slot)(upd, None) in {"Enabled", "Disabled"}
        attrs = sd._make_schedule_attrs(slot)(upd, None)
        assert "window" in attrs or attrs == {} or "start" in attrs


def test_required_top_level_fields_present(real_payload) -> None:
    """If any of these disappear, half the integration breaks. Loud fail."""
    required = {
        "state", "subState", "powerMeas", "voltMeas1", "currentSet",
        "sessionEnergy", "sessionMoney", "sessionTime", "totalEnergy",
        "IEM1", "IEM2", "tarif", "activeTarif", "aiStatus", "verFWMain",
    }
    missing = required - real_payload.keys()
    assert not missing, f"firmware drift: missing top-level fields {sorted(missing)}"


def test_validate_main_payload_accepts_real_payload(real_payload) -> None:
    assert validate_main_payload(real_payload, MODEL_16A) is real_payload


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (["not", "a", "dict"], "Expected dict, got list"),
        ({}, "Response missing required Eveus 'state' field"),
        ({"state": True, "currentSet": 16}, "Eveus 'state' field is boolean"),
        ({"state": float("inf"), "currentSet": 16}, "Eveus 'state' field is not finite"),
        ({"state": 2.5, "currentSet": 16}, "Eveus 'state' field is not an integer"),
        ({"state": "bad", "currentSet": 16}, "Eveus 'state' field is not numeric"),
        # 256 is outside the 0-255 byte range the state field can hold — unlike
        # 99, which firmware 1.x can legitimately report (see issue #11) and
        # validate_main_payload now accepts.
        ({"state": 256, "currentSet": 16}, "Eveus 'state' value 256 outside supported range"),
        ({"state": 2}, "Response missing required Eveus 'currentSet' field"),
        ({"state": 2, "currentSet": True}, "Eveus 'currentSet' field is boolean"),
        ({"state": 2, "currentSet": "bad"}, "Eveus 'currentSet' field is not numeric"),
        ({"state": 2, "currentSet": float("inf")}, "Eveus 'currentSet' field is not finite"),
        ({"state": 2, "currentSet": -1}, "Eveus 'currentSet' field below minimum"),
        (
            {"state": 2, "currentSet": 17},
            "Eveus 'currentSet' value 17.0 exceeds model maximum 16",
        ),
    ],
)
def test_validate_main_payload_rejects_invalid_payloads(payload, match) -> None:
    with pytest.raises(PayloadError, match=match):
        validate_main_payload(payload, MODEL_16A)
