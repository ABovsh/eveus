"""Every entity must read a classic-firmware payload exactly as it always has.

Support for newer firmware families is added at the coordinator boundary. This
pins the visible result for the payloads the integration has always served,
so a change made for one family cannot move a single reading, availability or
option of another: each of the 86 entities is built through the real setup
path, fed a recorded payload, and compared against the stored contract.

A deliberate change to a classic reading updates the contract file in the same
commit, where the diff shows exactly which entities moved.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from conftest import EV_HELPERS, HelperHass, disable_state_writes
import test_entity_inventory as inventory

_FIXTURES = Path(__file__).parent / "fixtures"
_CONTRACT = json.loads((_FIXTURES / "classic_entity_contract.json").read_text())
# Measured against the wall clock, so only its availability is pinned.
_CLOCK_DEPENDENT = frozenset({"eveus_time_drift"})


def _visible_value(entity: object) -> object:
    for attr in ("is_on", "current_option", "native_value"):
        if attr in dir(entity):
            value = getattr(entity, attr)
            if attr == "native_value" and hasattr(value, "isoformat"):
                value = value.isoformat()
            if isinstance(value, (str, int, float, bool, type(None))):
                return value
            return repr(value)
    return None


async def _capture(fixture: str) -> dict[str, dict[str, object]]:
    entry = inventory._entry()
    entry.runtime_data.updater.data = json.loads((_FIXTURES / fixture).read_text())
    hass = HelperHass(EV_HELPERS)
    captured: dict[str, dict[str, object]] = {}
    for setup_fn, _expected in inventory._EXPECTED.values():
        built: list[object] = []
        await setup_fn(None, entry, lambda entities, **_kw: built.extend(entities))
        for entity in built:
            entity.hass = hass
            disable_state_writes(entity)
            entity._maybe_finalize_device_info = lambda: None
            entity._handle_coordinator_update()
            captured[entity.unique_id] = {
                "available": bool(entity.available),
                "value": _visible_value(entity),
            }
    return captured


@pytest.mark.parametrize("fixture", sorted(_CONTRACT))
def test_classic_payload_entities_match_the_contract(fixture: str) -> None:
    captured = asyncio.run(_capture(fixture))
    expected = _CONTRACT[fixture]

    assert sorted(captured) == sorted(expected)
    for unique_id, want in expected.items():
        got = captured[unique_id]
        assert got["available"] == want["available"], unique_id
        if unique_id not in _CLOCK_DEPENDENT:
            assert got["value"] == want["value"], unique_id
