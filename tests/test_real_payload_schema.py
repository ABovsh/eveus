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

from custom_components.eveus import sensor_definitions as sd


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
    (sd.get_current_set, "currentSet", int),
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
    (sd.get_adaptive_current, "aiModecurrent", int),
    (sd.get_adaptive_voltage, "aiVoltage", int),
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


def test_session_time_and_system_time(real_payload) -> None:
    upd = _updater(real_payload)
    assert sd.get_session_time(upd, None) is not None
    assert sd.get_system_time(upd, None) is not None


def test_active_rate_resolves_to_known_slot(real_payload) -> None:
    upd = _updater(real_payload)
    # Whichever slot is active (0/1/2), the cost must resolve.
    assert sd.get_active_rate_cost(upd, None) == pytest.approx(4.32)


def test_adaptive_charging_state_resolves(real_payload) -> None:
    state = sd.get_adaptive_charging_state(_updater(real_payload), None)
    assert state in {"Active", "Idle"}


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
